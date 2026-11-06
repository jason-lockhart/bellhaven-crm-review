"""All CRM operations here use in-memory fakes or mocked HTTP sessions."""
import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from backend import matching, store, pipeline, scraper
from backend import app as review
from backend.crm_client import CRMClient, CRMError, PAGE_SIZE

COMMUNITY = dict(slug='altoona', name='Bellhaven Court of Altoona', address='10 North Main Street',
                 city='Altoona', state='PA', zip='16601', care_offerings=['Assisted Living'],
                 source_url='https://example.test/communities/altoona')
PARENT = dict(id='parent', name='Bellhaven Senior Living (Parent Account)', status='Active')
ACCOUNT = dict(id='facility', name=COMMUNITY['name'], street='10 N Main St', city='Altoona',
               state='PA', zip='16601', parent_id='wrong', lifetime_revenue=100,
               outstanding_ar=20, status='Active', notes='Keep billing history.', chow_current_account=None)


class FakeCRM:
    def __init__(self, account=None):
        self.accounts = {'parent': copy.deepcopy(PARENT), 'facility': copy.deepcopy(account or ACCOUNT)}
        self.creates = 0
        self.updates = []
        self.fail_link = False
        self.ignore_write = False
        self.timeout_create = False

    def list_accounts(self):
        return copy.deepcopy(list(self.accounts.values()))

    def get_account(self, account_id):
        if account_id not in self.accounts:
            raise CRMError('Not found')
        return copy.deepcopy(self.accounts[account_id])

    def create_account(self, payload):
        self.creates += 1
        self.accounts['successor'] = dict(payload, id='successor', lifetime_revenue=0, outstanding_ar=0)
        if self.timeout_create:
            raise CRMError('Timeout')
        return self.get_account('successor')

    def update_account(self, account_id, payload):
        self.updates.append((account_id, payload))
        if self.fail_link and 'chow_current_account' in payload:
            raise CRMError('Temporary link failure')
        if not self.ignore_write:
            self.accounts[account_id].update(payload)
        return self.get_account(account_id)


class DatabaseTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.dbpatch = patch.object(store, 'DATABASE_PATH', Path(self.directory.name)/'review.sqlite3')
        self.dbpatch.start()
        self.envpatch = patch.dict(os.environ, {'BELLHAVEN_REVIEW_PASSWORD': ''})
        self.envpatch.start()
        store.initialize()
        review.app.config['TESTING'] = True
        self.browser = review.app.test_client()

    def tearDown(self):
        self.envpatch.stop()
        self.dbpatch.stop()
        self.directory.cleanup()

    def proposal(self, account=None):
        return next(p for p in matching.build_proposals([COMMUNITY], [PARENT, account or ACCOUNT]) if p['type']=='parent_change')

    def apply(self, fake, proposal):
        with patch.object(review, 'CRMClient', return_value=fake):
            return review._apply_approved_proposal(proposal)


class FinancialAndWriteTests(DatabaseTest):
    def test_A_B_C_direct_reparent_and_D_successor(self):
        for revenue, ar, successor in [(0,0,False),(1,0,False),(0,1,False),(1,1,True)]:
            with self.subTest(revenue=revenue, ar=ar):
                account = dict(ACCOUNT, lifetime_revenue=revenue, outstanding_ar=ar)
                fake = FakeCRM(account)
                p = self.proposal(account)
                self.apply(fake, p)
                self.assertEqual(fake.creates, int(successor))
                self.assertEqual(fake.accounts['facility']['parent_id'], 'wrong' if successor else 'parent')
                self.assertEqual(fake.accounts['facility']['notes'], ACCOUNT['notes'])
                if successor:
                    self.assertEqual(fake.accounts['facility']['chow_current_account'], 'successor')
                    self.assertEqual(fake.accounts['successor']['parent_id'], 'parent')

    def test_E_partial_chow_retry_creates_only_once(self):
        fake = FakeCRM()
        p = self.proposal()
        store.upsert_proposals([p])
        fake.fail_link = True
        with self.assertRaises(CRMError):
            self.apply(fake, p)
        self.assertEqual(store.get_proposal(p['id'])['crm_result_account_id'], 'successor')
        self.assertEqual(fake.accounts['facility']['parent_id'], 'wrong')
        fake.fail_link = False
        self.apply(fake, store.get_proposal(p['id']))
        self.assertEqual(fake.creates, 1)

    def test_F_valid_existing_successor_is_reused(self):
        fake = FakeCRM()
        p = self.proposal()
        fake.accounts['successor'] = dict(review._create_payload(p), id='successor')
        fake.accounts['facility']['chow_current_account'] = 'successor'
        self.assertEqual(self.apply(fake,p), 'successor')
        self.assertEqual(fake.creates, 0)
        self.assertEqual(fake.updates, [])

    def test_existing_invalid_successor_blocks_write(self):
        fake = FakeCRM()
        p = self.proposal()
        fake.accounts['facility']['chow_current_account'] = 'parent'
        with self.assertRaises(CRMError): self.apply(fake,p)
        self.assertEqual(fake.creates,0)

    def test_G_wrong_or_nonunique_parent_blocks_write(self):
        for parent in [None, dict(PARENT,id='other')]:
            fake = FakeCRM()
            if parent: fake.accounts['other']=parent
            else: del fake.accounts['parent']
            with self.assertRaises(CRMError): self.apply(fake,self.proposal())
            self.assertEqual(fake.creates,0)
            self.assertEqual(fake.updates,[])

    def test_missing_unparseable_nonfinite_financials_block_all_cases(self):
        for value in [None,'','unknown','NaN','Infinity',-1,True]:
            for field in ['lifetime_revenue','outstanding_ar']:
                with self.subTest(value=value,field=field):
                    account=dict(ACCOUNT, **{field:value})
                    fake=FakeCRM(account)
                    with self.assertRaises(CRMError): self.apply(fake,self.proposal(account))
                    self.assertEqual(fake.creates,0)
                    self.assertEqual(fake.updates,[])
        with self.assertRaises(ValueError):
            matching.requires_successor_account(dict(lifetime_revenue=0,outstanding_ar=None))

    def test_current_financials_rechecked_before_write(self):
        fake=FakeCRM(dict(ACCOUNT,outstanding_ar=0))
        with self.assertRaises(CRMError): self.apply(fake,self.proposal())
        self.assertEqual(fake.updates,[])

    def test_identity_or_parent_drift_blocks_write(self):
        for changes in [dict(street='999 Different Road'),dict(parent_id='third')]:
            fake=FakeCRM(dict(ACCOUNT,**changes))
            with self.assertRaises(CRMError): self.apply(fake,self.proposal())
            self.assertEqual(fake.creates,0)

    def test_unverified_write_is_not_success(self):
        fake=FakeCRM(dict(ACCOUNT,outstanding_ar=0));fake.ignore_write=True
        with self.assertRaises(CRMError): self.apply(fake,self.proposal(dict(ACCOUNT,outstanding_ar=0)))

    def test_successor_verified_before_linking(self):
        fake=FakeCRM();p=self.proposal()
        fake.accounts['successor']=dict(review._create_payload(p),id='successor',parent_id='wrong')
        with self.assertRaises(CRMError): self.apply(fake,p)
        self.assertEqual(fake.updates,[])

    def test_timeout_after_creation_recovers_marker_without_second_post(self):
        fake=FakeCRM();fake.timeout_create=True;p=self.proposal()
        with self.assertRaises(CRMError): self.apply(fake,p)
        p['crm_write_error']='Creation outcome unknown'
        self.apply(fake,p)
        self.assertEqual(fake.creates,1)

    def test_unknown_creation_outcome_without_marker_cannot_repost(self):
        fake=FakeCRM();p=self.proposal();p['crm_write_error']='Creation outcome unknown'
        with self.assertRaises(CRMError): self.apply(fake,p)
        self.assertEqual(fake.creates,0)


class ReconciliationTests(DatabaseTest):
    def test_live_field_aliases_match_existing_without_proposals(self):
        account={'account_id':'facility','name':COMMUNITY['name'],'billing_street':COMMUNITY['address'],
                 'billing_city':'Altoona','billing_state':'PA','billing_zip':'16601-1234','parent_id':'parent'}
        result=matching.build_proposals([COMMUNITY],[dict(PARENT,account_id='parent'),account])
        self.assertNotIn('missing_account',[p['type'] for p in result])
        self.assertNotIn('parent_change',[p['type'] for p in result])

    def test_correct_existing_account_has_no_proposal(self):
        self.assertEqual(matching.build_proposals([COMMUNITY],[PARENT,dict(ACCOUNT,parent_id='parent')]),[])

    def test_rebrand_matches_address_and_proposes_name_correction(self):
        result=matching.build_proposals([COMMUNITY],[PARENT,dict(ACCOUNT,name='Legacy Nursing Center',parent_id='parent')])
        self.assertEqual([p['type'] for p in result],['name_update'])

    def test_same_name_different_house_is_ambiguous(self):
        result=matching.build_proposals([COMMUNITY],[PARENT,dict(ACCOUNT,street='99 Other Road')])
        self.assertEqual(result[0]['type'],'ambiguous_match')
        self.assertTrue(any('Candidate facility' in e for e in result[0]['evidence']))

    def test_duplicate_requires_same_physical_location(self):
        result=matching.build_proposals([COMMUNITY],[PARENT,ACCOUNT,dict(ACCOUNT,id='duplicate')])
        self.assertEqual([p['type'] for p in result],['duplicate_account'])
        fake=FakeCRM();fake.accounts['duplicate']=dict(ACCOUNT,id='duplicate')
        self.apply(fake,result[0])
        loser=fake.accounts[result[0]['account']['id']]
        self.assertEqual(loser['status'],'Inactive')
        self.assertIn('billing history preserved',loser['notes'])
        self.assertIn('Keep billing history',loser['notes'])

    def test_missing_and_unresolved_parent(self):
        result=matching.build_proposals([COMMUNITY],[PARENT])
        self.assertEqual(result[0]['type'],'missing_account')
        self.assertEqual(result[0]['changes'][0]['after'],'parent')
        self.assertIsNone(matching.identify_parent([PARENT,dict(PARENT,id='second')]))
        result=matching.build_proposals([COMMUNITY],[ACCOUNT])
        self.assertEqual(result[0]['type'],'ambiguous_match')
        self.assertEqual(result[0]['changes'],[])

    def test_pending_legacy_invalidated_decisions_preserved(self):
        legacy=dict(id='legacy',type='missing_account',title='Bad proposal')
        rejected=dict(id='rejected',type='missing_account',title='Decision')
        store.upsert_proposals([legacy,rejected])
        store.set_decision('rejected','rejected','Reviewed',write_status='not_applicable')
        store.upsert_proposals([self.proposal()])
        self.assertEqual(store.get_proposal('legacy')['status'],'superseded')
        self.assertEqual(store.get_proposal('rejected')['status'],'rejected')

    def test_repeat_runs_keep_rejection_until_facts_change(self):
        p=self.proposal();store.upsert_proposals([p]);store.set_decision(p['id'],'rejected','Investigate')
        self.assertEqual(store.upsert_proposals([self.proposal()]),(0,1))
        self.assertEqual(store.get_proposal(p['id'])['status'],'rejected')
        self.assertNotEqual(self.proposal(dict(ACCOUNT,parent_id='third'))['id'],p['id'])
        self.assertEqual(store.recent_activity()[0]['decision'],'rejected')

    def test_successful_chow_does_not_reappear_or_become_duplicate(self):
        fake=FakeCRM();self.apply(fake,self.proposal())
        result=matching.build_proposals([COMMUNITY],fake.list_accounts())
        self.assertEqual(result,[])

    def test_scan_is_read_only(self):
        fake=FakeCRM()
        with patch.object(pipeline.scraper,'scrape_communities',return_value=[COMMUNITY]),patch.object(pipeline.crm_client,'token_is_configured',return_value=True),patch.object(pipeline.crm_client,'CRMClient',return_value=fake):
            first=pipeline.run_reconciliation();second=pipeline.run_reconciliation()
        self.assertEqual(first['proposals_created'],1)
        self.assertEqual(second['proposals_created'],0)
        self.assertEqual(fake.creates,0);self.assertEqual(fake.updates,[])


class ApprovalEndpointTests(DatabaseTest):
    def decide(self,p,decision='approved',**kwargs):
        return self.browser.post(f"{review.API_PREFIX}/proposals/{p['id']}/decision",json={'decision':decision},**kwargs)

    def test_approval_verified_persisted_and_duplicate_submission_noop(self):
        p=self.proposal();store.upsert_proposals([p]);fake=FakeCRM()
        with patch.object(review,'CRMClient',return_value=fake),patch.object(review.crm_client,'token_is_configured',return_value=True):
            first=self.decide(p);second=self.decide(p)
        self.assertTrue(first.json['crm_updated']);self.assertTrue(second.json['crm_updated'])
        self.assertEqual(fake.creates,1)
        self.assertEqual(store.recent_activity()[0]['crm_write_status'],'applied')

    def test_failed_verification_is_recorded_as_failed(self):
        account=dict(ACCOUNT,outstanding_ar=0);p=self.proposal(account);store.upsert_proposals([p]);fake=FakeCRM(account);fake.ignore_write=True
        with patch.object(review,'CRMClient',return_value=fake),patch.object(review.crm_client,'token_is_configured',return_value=True):
            response=self.decide(p)
        self.assertFalse(response.json['crm_updated'])
        self.assertEqual(response.json['proposal']['crm_write_status'],'failed')

    def test_atomic_claim_prevents_concurrent_writes(self):
        p=self.proposal();store.upsert_proposals([p])
        self.assertTrue(store.claim_approval(p['id'],'Approved'))
        self.assertFalse(store.claim_approval(p['id'],'Again'))
        with patch.object(review,'CRMClient') as client:
            self.assertEqual(self.decide(p).status_code,409)
        client.assert_not_called()

    def test_rejection_and_superseded_cannot_write(self):
        p=self.proposal();store.upsert_proposals([p])
        with patch.object(review,'CRMClient') as client:
            self.assertFalse(self.decide(p,'rejected').json['crm_updated'])
            self.assertEqual(self.decide(p).status_code,409)
        client.assert_not_called()
        self.assertEqual(store.upsert_proposals([p]),(0,1))

    def test_csrf_and_auth_guards(self):
        p=self.proposal();store.upsert_proposals([p])
        self.assertEqual(self.decide(p,headers={'Origin':'https://evil.test'}).status_code,403)
        self.assertEqual(self.decide(p,headers={'Sec-Fetch-Site':'cross-site'}).status_code,403)
        self.assertEqual(self.browser.post(review.API_PREFIX+'/run',data='decision=approved').status_code,415)
        with patch.dict(os.environ,{'BELLHAVEN_REVIEW_PASSWORD':'test-only-password'}):
            self.assertEqual(self.browser.get(review.API_PREFIX+'/health').status_code,401)
            self.assertEqual(self.browser.get(review.API_PREFIX+'/health',auth=('reviewer','test-only-password')).status_code,200)


class CRMHTTPTests(unittest.TestCase):
    def response(self,payload,status=200):
        result=Mock(ok=status<400,status_code=status,content=b'json')
        result.json.return_value=payload
        return result

    def test_pagination_and_live_aliases(self):
        session=Mock();session.get.side_effect=[self.response({'accounts':[{'account_id':str(i)} for i in range(PAGE_SIZE)]}),self.response({'accounts':[{'account_id':'last'}]})]
        result=CRMClient('fake-token',session).list_accounts()
        self.assertEqual(len(result),PAGE_SIZE+1)
        self.assertEqual(session.get.call_args.kwargs['params']['page'],2)

    def test_malformed_json_http_timeout_missing_id_and_repeated_page(self):
        for response in [self.response({},200),self.response([],403),self.response([{}])]:
            session=Mock();session.get.return_value=response
            with self.assertRaises(CRMError): CRMClient('fake-token',session).list_accounts()
        session=Mock();session.get.side_effect=requests.Timeout('timed out')
        with self.assertRaises(CRMError): CRMClient('fake-token',session).list_accounts()
        session=Mock();response=self.response(None);response.json.side_effect=ValueError();session.get.return_value=response
        with self.assertRaises(CRMError): CRMClient('fake-token',session).list_accounts()
        session=Mock();session.get.return_value=self.response([{'account_id':str(i)} for i in range(PAGE_SIZE)])
        with self.assertRaises(CRMError): CRMClient('fake-token',session).list_accounts()

    def test_get_identifier_validation_and_write_errors(self):
        session=Mock();session.get.return_value=self.response({'account_id':'wrong'})
        with self.assertRaises(CRMError): CRMClient('fake-token',session).get_account('expected')
        for method in ['post','patch']:
            session=Mock();getattr(session,method).return_value=self.response({},500)
            client=CRMClient('fake-token',session)
            with self.assertRaises(CRMError):
                if method=='post': client.create_account({'name':'test'})
                else: client.update_account('id',{'status':'Active'})


class CrawlTests(unittest.TestCase):
    def test_all_pages_homepage_extra_findlay_and_details(self):
        base=scraper.WEBSITE_ROOT
        detail='<h1>Community</h1><dl class="detail"><dt>Address</dt><dd>10 Main St<br>City, OH 44000</dd><dt>Care Offerings</dt><dd><span class="badge">Memory Care</span></dd></dl>'
        pages={base+'/':'<a href="/communities/findlay">Findlay</a><a href="/communities">Directory</a>',base+'/communities':'<a href="/communities/one">One</a><a href="/communities?page=2">Next</a>',base+'/communities?page=2':'<a href="/communities/two">Two</a><a href="/communities">Previous</a>'}
        session=Mock()
        def get(url,**kwargs):
            response=Mock(url=url,text=pages.get(url,detail));return response
        session.get.side_effect=get
        result=scraper.scrape_communities(session)
        self.assertEqual({c['slug'] for c in result},{'findlay','one','two'})
        self.assertTrue(all(c['care_offerings']==['Memory Care'] for c in result))

    def test_network_failure_and_incomplete_detail_abort_scan(self):
        session=Mock();session.get.side_effect=requests.Timeout()
        with self.assertRaises(scraper.ScrapeError): scraper.scrape_communities(session)
        with self.assertRaises(scraper.ScrapeError): scraper.parse_community_detail('<h1>Incomplete</h1>','https://example.test/a')


class AdditionalRegressionTests(DatabaseTest):
    def test_superseded_undecided_proposal_can_return(self):
        p=self.proposal();store.upsert_proposals([p]);store.upsert_proposals([])
        self.assertEqual(store.get_proposal(p['id'])['status'],'superseded')
        store.upsert_proposals([p])
        self.assertEqual(store.get_proposal(p['id'])['status'],'pending')

    def test_rejection_cannot_overwrite_claimed_approval(self):
        p=self.proposal();store.upsert_proposals([p])
        self.assertTrue(store.claim_approval(p['id'],'approved'))
        self.assertFalse(store.record_rejection(p['id'],'racing rejection'))
        self.assertEqual(store.get_proposal(p['id'])['status'],'approved')

    def test_global_write_claim_serializes_different_proposals(self):
        first=self.proposal();second=dict(first,id='second')
        store.upsert_proposals([first,second])
        self.assertTrue(store.claim_approval(first['id'],None))
        self.assertFalse(store.claim_approval(second['id'],None))

    def test_legacy_rejection_is_not_recreated_by_new_fingerprint(self):
        current=self.proposal();legacy=dict(current,id='old-hash');legacy.pop('matcher_version')
        store.upsert_proposals([legacy]);store.set_decision('old-hash','rejected','Reviewed')
        self.assertEqual(store.upsert_proposals([current]),(0,1))
        self.assertIsNone(store.get_proposal(current['id']))

    def test_invalid_successor_link_does_not_make_facility_missing(self):
        result=matching.build_proposals([COMMUNITY],[PARENT,dict(ACCOUNT,chow_current_account='nonexistent')])
        self.assertNotIn('missing_account',[p['type'] for p in result])

    def test_missing_reviewer_password_blocks_decisions_outside_tests(self):
        review.app.config['TESTING']=False
        try:
            response=self.browser.post(review.API_PREFIX+'/proposals/any/decision',json={'decision':'approved'})
            self.assertEqual(response.status_code,503)
        finally:
            review.app.config['TESTING']=True

    def test_missing_account_creation_verified_and_retry_no_duplicate(self):
        p=matching.build_proposals([COMMUNITY],[PARENT])[0]
        fake=FakeCRM();del fake.accounts['facility'];store.upsert_proposals([p])
        self.assertEqual(self.apply(fake,p),'successor')
        self.assertEqual(self.apply(fake,p),'successor')
        self.assertEqual(fake.creates,1)

    def test_now_existing_facility_blocks_missing_account_creation(self):
        p=matching.build_proposals([COMMUNITY],[PARENT])[0];fake=FakeCRM()
        with self.assertRaises(CRMError): self.apply(fake,p)
        self.assertEqual(fake.creates,0)

    def test_stale_child_requires_review_without_inactivation(self):
        result=matching.build_proposals([], [PARENT,dict(ACCOUNT,parent_id='parent')])
        self.assertEqual(result[0]['type'],'stale_account')
        self.assertEqual(result[0]['changes'][0]['after'],'Needs Review')


    def test_already_corrected_parent_never_creates_unneeded_successor(self):
        p=self.proposal();fake=FakeCRM(dict(ACCOUNT,parent_id='parent'))
        self.assertEqual(self.apply(fake,p),'facility')
        self.assertEqual(fake.creates,0)
        self.assertEqual(fake.updates,[])

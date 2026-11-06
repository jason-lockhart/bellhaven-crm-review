from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from backend import pipeline, store
from backend.crm_client import _api_account_payload, normalize_account_record
from backend.matching import (
    build_proposals,
    normalize_address,
    normalize_name,
    requires_successor_account,
    score_account,
)
from backend.scraper import parse_community_detail, parse_listing_page


class CRMFieldAdapterTests(unittest.TestCase):
    def test_maps_live_account_fields_to_matcher_fields(self):
        account = normalize_account_record(
            {
                "account_id": "crm-123",
                "name": "Bellhaven of Maplewood",
                "billing_street": "210 Orchard Lane",
                "billing_city": "Maplewood",
                "billing_state": "OH",
                "billing_zip": "44280",
                "note": "Reviewed last quarter.",
            }
        )
        self.assertEqual(account["id"], "crm-123")
        self.assertEqual(account["street"], "210 Orchard Lane")
        self.assertEqual(account["city"], "Maplewood")
        self.assertEqual(account["state"], "OH")
        self.assertEqual(account["zip"], "44280")
        self.assertEqual(account["notes"], "Reviewed last quarter.")

    def test_maps_approved_fields_back_to_live_api_names(self):
        payload = _api_account_payload(
            {
                "name": "Bellhaven of Maplewood",
                "street": "210 Orchard Lane",
                "city": "Maplewood",
                "state": "OH",
                "zip": "44280",
                "parent_id": "parent-123",
                "status": "Active",
                "notes": "Created after approval.",
            }
        )
        self.assertEqual(
            payload,
            {
                "name": "Bellhaven of Maplewood",
                "billing_street": "210 Orchard Lane",
                "billing_city": "Maplewood",
                "billing_state": "OH",
                "billing_zip": "44280",
                "parent_id": "parent-123",
                "status": "Active",
                "note": "Created after approval.",
            },
        )


class MatchingTests(unittest.TestCase):
    def test_normalizes_company_and_address_variants(self):
        self.assertEqual(normalize_name("Bellhaven Living, Inc."), "bellhaven living")
        self.assertEqual(normalize_address("210 Orchard Lane"), "210 orchard ln")

    def test_exact_location_scores_with_address_evidence(self):
        community = {
            "name": "Bellhaven of Maplewood",
            "address": "210 Orchard Lane",
            "city": "Maplewood",
            "state": "OH",
            "zip": "44280",
        }
        account = {
            "name": "Bellhaven of Maplewood",
            "street": "210 Orchard Ln",
            "city": "Maplewood",
            "state": "OH",
            "zip": "44280",
        }
        confidence, evidence = score_account(community, account)
        self.assertGreaterEqual(confidence, 0.99)
        self.assertTrue(any("Street address matches" in item for item in evidence))

    def test_close_names_with_conflicting_locations_are_not_high_confidence(self):
        community = {
            "name": "Bellhaven of Maplewood",
            "address": "210 Orchard Lane",
            "city": "Maplewood",
            "state": "OH",
            "zip": "44280",
        }
        account = {
            "name": "Bellhaven of Maplewood",
            "street": "99 Other Road",
            "city": "Cleveland",
            "state": "OH",
            "zip": "44101",
        }
        confidence, evidence = score_account(community, account)
        self.assertLess(confidence, 0.8)
        self.assertIn("City differs.", evidence)

    def test_missing_location_evidence_reduces_name_only_confidence(self):
        community = {"name": "Bellhaven of Maplewood"}
        account = {"name": "Bellhaven of Maplewood"}
        confidence, evidence = score_account(community, account)
        self.assertEqual(confidence, 0.55)
        self.assertIn("ZIP code evidence is incomplete.", evidence)

    def test_parent_change_uses_revenue_and_ar_guard(self):
        community = {
            "slug": "maplewood",
            "name": "Bellhaven of Maplewood",
            "address": "210 Orchard Lane",
            "city": "Maplewood",
            "state": "OH",
            "zip": "44280",
            "care_offerings": ["Assisted Living"],
            "source_url": "https://example.test/communities/maplewood",
        }
        accounts = [
            {"id": "parent", "name": "Bellhaven Senior Living"},
            {
                "id": "location",
                "name": "Bellhaven of Maplewood",
                "street": "210 Orchard St",
                "city": "Maplewood",
                "state": "OH",
                "zip": "44280",
                "parent_id": "wrong-parent",
                "lifetime_revenue": 1200,
                "outstanding_ar": 400,
            },
        ]
        proposals = build_proposals([community], accounts)
        parent_change = next(proposal for proposal in proposals if proposal["type"] == "parent_change")
        self.assertEqual(parent_change["account"]["lifetime_revenue"], 1200)
        self.assertEqual(parent_change["account"]["outstanding_ar"], 400)
        self.assertEqual(parent_change["changes"][0]["after"], "parent")
        # The financial rule is enforced by the explicit approval/apply path,
        # not by the read-only matching stage.

    def test_recognizes_live_parent_account_label(self):
        community = {
            "slug": "maplewood",
            "name": "Bellhaven of Maplewood",
            "address": "210 Orchard Lane",
            "city": "Maplewood",
            "state": "OH",
            "zip": "44280",
        }
        parent = {"id": "parent-123", "name": "Bellhaven Senior Living (Parent Account)"}
        location = {
            "id": "location-123",
            "name": community["name"],
            "street": community["address"],
            "city": community["city"],
            "state": community["state"],
            "zip": community["zip"],
            "parent_id": "wrong-parent",
            "lifetime_revenue": 0,
            "outstanding_ar": 0,
        }
        proposals = build_proposals([community], [parent, location])
        self.assertEqual([proposal["type"] for proposal in proposals], ["parent_change"])
        self.assertEqual(proposals[0]["changes"][0]["after"], "parent-123")

    def test_successor_rule_requires_both_positive_balances(self):
        self.assertTrue(
            requires_successor_account({"lifetime_revenue": 1, "outstanding_ar": 0.01})
        )
        self.assertFalse(
            requires_successor_account({"lifetime_revenue": 1, "outstanding_ar": 0})
        )
        self.assertFalse(
            requires_successor_account({"lifetime_revenue": 0, "outstanding_ar": 1})
        )

    def test_unmatched_children_are_proposed_instead_of_deleted(self):
        communities = []
        accounts = [
            {"id": "parent", "name": "Bellhaven Senior Living"},
            {"id": "old", "name": "Bellhaven of Oldtown", "parent_id": "parent", "status": "Active"},
        ]
        proposals = build_proposals(communities, accounts)
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["type"], "stale_account")
        self.assertEqual(proposals[0]["changes"][0]["after"], "Inactive")


class ScraperTests(unittest.TestCase):
    def test_listing_parser_finds_location_and_next_page(self):
        html = """
        <div class="card">
          <h3><a href="/communities/bellhaven-of-maplewood">Bellhaven of Maplewood</a></h3>
        </div>
        <div class="pager"><a href="/communities?page=2">Next</a></div>
        """
        communities, next_pages = parse_listing_page(
            html, "https://analyst-assessment-production.up.railway.app/communities?page=1"
        )
        self.assertEqual(communities[0]["slug"], "bellhaven-of-maplewood")
        self.assertEqual(
            next_pages,
            ["https://analyst-assessment-production.up.railway.app/communities?page=2"],
        )

    def test_detail_parser_extracts_address_and_multiple_care_offerings(self):
        html = """
        <h1>Bellhaven Shores of Erie</h1>
        <dl class="detail">
          <dt>Address</dt><dd>12 Bay Road<br>Erie, PA 16501</dd>
          <dt>Care Offerings</dt><dd><span class="badge">Assisted Living</span>
          <span class="badge">Memory Support</span></dd>
        </dl>
        """
        community = parse_community_detail(
            html, "https://example.test/communities/bellhaven-shores-of-erie"
        )
        self.assertEqual(community["address"], "12 Bay Road")
        self.assertEqual(community["city"], "Erie")
        self.assertEqual(community["state"], "PA")
        self.assertEqual(community["zip"], "16501")
        self.assertEqual(community["care_offerings"], ["Assisted Living", "Memory Support"])


class PersistenceTests(unittest.TestCase):
    def test_repeat_runs_preserve_existing_decisions(self):
        proposal = {
            "id": "stable-proposal-id",
            "type": "missing_account",
            "status": "pending",
            "title": "Create one location",
            "reviewer_notes": None,
            "created_at": None,
        }
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(store, "DATABASE_PATH", Path(directory) / "review.sqlite3"):
                store.initialize()
                self.assertEqual(store.upsert_proposals([proposal]), (1, 0))
                approved = store.set_decision(
                    proposal["id"],
                    "approved",
                    "Reviewed with source evidence.",
                    write_status="waiting_for_token",
                )
                self.assertEqual(approved["status"], "approved")
                self.assertEqual(store.upsert_proposals([proposal]), (0, 1))
                saved = store.get_proposal(proposal["id"])
                self.assertEqual(saved["status"], "approved")
                self.assertEqual(saved["reviewer_notes"], "Reviewed with source evidence.")
                self.assertEqual(saved["crm_write_status"], "waiting_for_token")


class ApprovalRuleTests(unittest.TestCase):
    def _proposal(self, revenue, ar):
        return {
            "id": "parent-change-test",
            "type": "parent_change",
            "account": {
                "id": "old-account",
                "name": "Bellhaven of Maplewood",
                "street": "210 Orchard Lane",
                "city": "Maplewood",
                "state": "OH",
                "zip": "44280",
                "status": "Active",
                "notes": "",
                "lifetime_revenue": revenue,
                "outstanding_ar": ar,
                "chow_current_account": None,
            },
            "community": {
                "name": "Bellhaven of Maplewood",
                "address": "210 Orchard Lane",
                "city": "Maplewood",
                "state": "OH",
                "zip": "44280",
            },
            "changes": [{"field": "parent_id", "before": "old-parent", "after": "new-parent"}],
        }

    def test_approved_parent_change_preserves_account_when_both_balances_are_positive(self):
        from backend import app as review_app

        client = Mock()
        client.list_accounts.return_value = [{"id": "old-account", "notes": ""}]
        client.create_account.return_value = {"id": "successor-account"}
        with patch("backend.app.CRMClient", return_value=client), patch(
            "backend.app.store.set_write_status", return_value={}
        ):
            result = review_app._apply_approved_proposal(self._proposal(100, 25))
        self.assertEqual(result, "successor-account")
        client.create_account.assert_called_once()
        client.update_account.assert_called_once_with(
            "old-account", {"chow_current_account": "successor-account"}
        )

    def test_parent_change_updates_existing_account_if_either_balance_is_zero(self):
        from backend import app as review_app

        client = Mock()
        with patch("backend.app.CRMClient", return_value=client):
            result = review_app._apply_approved_proposal(self._proposal(100, 0))
        self.assertEqual(result, "old-account")
        client.create_account.assert_not_called()
        client.update_account.assert_called_once_with(
            "old-account", {"parent_id": "new-parent"}
        )

    def test_initial_pipeline_stages_website_data_without_a_crm_token(self):
        community = {
            "slug": "maplewood",
            "name": "Bellhaven of Maplewood",
            "address": "210 Orchard Lane",
            "city": "Maplewood",
            "state": "OH",
            "zip": "44280",
            "care_offerings": ["Assisted Living"],
            "source_url": "https://example.test/communities/maplewood",
        }
        with patch("backend.pipeline.scraper.scrape_communities", return_value=[community]), patch(
            "backend.pipeline.store.save_communities"
        ), patch("backend.pipeline.crm_client.token_is_configured", return_value=False), patch(
            "backend.pipeline.crm_client.CRMClient"
        ) as client_class:
            result = pipeline.run_reconciliation()
        self.assertEqual(result["status"], "crm_not_configured")
        self.assertEqual(result["communities_scanned"], 1)
        client_class.assert_not_called()


class DecisionRouteTests(unittest.TestCase):
    def setUp(self):
        from backend import app as review_app

        self.review_app = review_app
        self.temp_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_directory.name) / "review.sqlite3"
        self.database_patch = patch.object(store, "DATABASE_PATH", self.database_path)
        self.database_patch.start()
        store.initialize()
        store.upsert_proposals(
            [
                {
                    "id": "decision-route-test",
                    "type": "missing_account",
                    "status": "pending",
                    "confidence": 0.9,
                    "risk_level": "high",
                    "title": "Create a test location",
                    "summary": "A test proposal.",
                    "community": None,
                    "account": None,
                    "changes": [{"field": "parent_id", "before": None, "after": "parent"}],
                    "evidence": ["Test evidence."],
                    "manual_review": False,
                    "reviewer_notes": None,
                    "created_at": None,
                }
            ]
        )
        self.client = self.review_app.app.test_client()

    def tearDown(self):
        self.database_patch.stop()
        self.temp_directory.cleanup()

    def test_rejection_is_persisted_without_a_crm_request(self):
        with patch("backend.app.CRMClient") as crm_client_class:
            response = self.client.post(
                "/api/bellhaven/review/proposals/decision-route-test/decision",
                json={"decision": "rejected", "reviewer_notes": "Not a verified location."},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["proposal"]["status"], "rejected")
        self.assertFalse(response.json["crm_updated"])
        crm_client_class.assert_not_called()
        self.assertEqual(store.get_proposal("decision-route-test")["status"], "rejected")

    def test_approval_without_token_is_saved_but_never_writes(self):
        with patch(
            "backend.app.crm_client.token_is_configured", return_value=False
        ), patch("backend.app.CRMClient") as crm_client_class:
            response = self.client.post(
                "/api/bellhaven/review/proposals/decision-route-test/decision",
                json={"decision": "approved"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["proposal"]["status"], "approved")
        self.assertEqual(response.json["proposal"]["crm_write_status"], "waiting_for_token")
        self.assertFalse(response.json["crm_updated"])
        crm_client_class.assert_not_called()


if __name__ == "__main__":
    unittest.main()

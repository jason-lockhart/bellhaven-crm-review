# Walkthrough and live review guide

## Before any approval

1. Synchronize the reviewed branch into Replit, back up SQLite, rebuild and restart. Confirm you are viewing port 8000 and the new code, not an old preview.
2. Configure the CRM token and reviewer password through Replit Secrets. Keep HTTPS preview private.
3. Run a read-only reconciliation. Confirm all 35 expected website communities (including Findlay), the complete paginated CRM account inventory, and a unique Bellhaven corporate ID. Investigate unexpected counts rather than approving missing accounts in bulk.
4. Spot-check Altoona, Monroe, Sycamore Ridge and Ashtabula: existing correct locations must not be proposed for creation. Check Lima, Findlay, Marietta, Tiffin and the Owosso duplicates individually using source addresses and current CRM IDs. These are leads, not permission to move them.
5. Read the full source record, CRM snapshot, candidate evidence, confidence, risk and field changes. Open the website evidence in a full Safari tab. Confirm location identity beyond a similar name.
6. For parent corrections, verify the corporate ID and **both** financial values. Both positive means a new successor; zero in either means direct re-parent; unknown means stop. Check any existing successor link before proceeding.
7. For duplicates, confirm both physical locations, survivor ID, notes and billing implications. For stale records, absence from the directory is only a Needs Review signal.
8. Approve one verified correction. Confirm `CRM: applied` in history, then independently open that account in the CRM browser. For CHOW, verify the new account's parent and the old account's unchanged parent plus successor link. A recorded approval or failed write is not a corrected CRM record.
9. Reject one unsuitable proposal with a reason. Run reconciliation again; unchanged rejected decisions must remain rejected and completed writes must not repeat.
10. Review the remaining valid corrections individually until the intended assessment CRM end state is actually reached. Do not submit a queue full of unapproved proposals as a corrected CRM.

## Two-minute screen recording on iPad

- 0:00–0:25: Show the inventory and completed scan; open one proposal's source URL and address evidence.
- 0:25–0:55: Explain its current CRM ID/parent, confidence, changes and the financial safeguard. Approve only if this is a correction you explicitly intend to make.
- 0:55–1:20: Open the CRM browser and show the confirmed resulting record (and old/new CHOW records if applicable).
- 1:20–1:40: Reject a different investigated proposal with notes and show the decision log including CRM write status.
- 1:40–2:00: Rerun reconciliation, show no repeated successful write or recreated unchanged rejection, and summarize one tradeoff.

Use iPad Control Center → Screen Recording. Keep Secrets and authorization headers off-screen. The video requires your live Replit/CRM session; the code repair cannot produce evidence of changes that have not happened.

## 45-minute technical discussion

Trace `scraper.py` → `crm_client.py` → `matching.py` → `pipeline.py` → `store.py`. Then trace the approval route in `app.py`, emphasizing current-state reads, corporate ID checks, financial validation, creation uncertainty, readback verification and the atomic write claim. Run `test_safety.py` and explain the partial CHOW retry test.

For a small live coding change, add a street abbreviation to `ADDRESS_WORDS` in `matching.py`, demonstrate normalization with a synthetic example, then run the tests. This is explainable and needs no live CRM write. Avoid adjusting a confidence threshold just to make the queue smaller.

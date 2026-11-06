# Bellhaven CRM Review

Flask + React review application for Clipboard Health's Bellhaven assessment. Scans scrape the public website and read the CRM. Only an authenticated human approval can apply a proposal. No live CRM changes were made during this repair.

## Audit findings

The initial matcher expected `id`, `street`, `city`, `state`, and `zip`. The real API uses `account_id` and `billing_*`. As a result, account records could be excluded entirely, leaving every community classified as missing and no corporate parent. The initial parent name comparison also excluded `(Parent Account)`. Commit `3dad6a7` partially fixed the adapter and parent name before this audit; it did **not** invalidate the 35 already-staged proposals. Thus the dashboard could continue showing the defective results. Without the Replit database or a live token, the exact runtime/version combination that produced those 35 rows cannot be independently proven.

This repair normalizes input at the matcher boundary as well as the HTTP boundary, recognizes the unique corporate label, rejects malformed/incomplete account responses, and supersedes undecided obsolete proposals after a successful scan. Genuine approvals/rejections remain in history. Legacy proposals cannot execute writes. Parent IDs are discovered from current records, never hardcoded. Duplicate corporate candidates block ownership writes.

Other repaired defects: missing financial values treated as zero; snapshot-only financial checks; no independent write verification; race conditions on repeated approvals; unsafe stale-account inactivation; lost duplicate explanations; broken history query; and missing committed routing between React and Flask.

## iPad / Replit: synchronize and run

Use Safari's full Replit editor (Request Desktop Website if Shell is hidden). No desktop installation or paid AI Agent is needed.

1. Stop the running application. In Replit Git, commit or separately preserve any local source changes. Do not discard conflicts or overwrite an uncommitted project.
2. Back up the decision database **inside private Replit storage**, with the app stopped. Never upload it to GitHub. From the repository root, use the Python SQLite backup command below.
3. In Replit Shell, fetch the reviewed branch, or merge its PR on GitHub and pull `main`. GitHub changes do not update a running Replit preview automatically.

```bash
# From the repository root; creates a private local backup if a DB exists.
python - <<'PY'
import os, sqlite3
from pathlib import Path
p = Path(os.environ.get('BELLHAVEN_DB_PATH', 'artifacts/bellhaven-crm-review/backend/data/review.sqlite3'))
if p.exists():
    with sqlite3.connect(p) as source, sqlite3.connect(str(p) + '.backup') as target:
        source.backup(target)
    print('Decision database backed up locally.')
PY
git fetch origin
git switch codex/fix-bellhaven-reconciliation
git pull --ff-only origin codex/fix-bellhaven-reconciliation
# After the PR is merged, alternatively: git switch main && git pull --ff-only origin main
python -m pip install -r artifacts/bellhaven-crm-review/backend/requirements.txt
pnpm install --frozen-lockfile
pnpm run typecheck:libs
PORT=5173 BASE_PATH=/ pnpm --filter @workspace/bellhaven-crm-review build
cd artifacts/bellhaven-crm-review
python -m unittest discover -s backend/tests -v
PORT=8000 python -m backend.app
```

4. In Replit Secrets retain `CLIPBOARD_API_TOKEN` and add `BELLHAVEN_REVIEW_PASSWORD` with a unique reviewer password. Never put either value in source, shell command arguments, chat, or the README. If a custom `BELLHAVEN_DB_PATH` was used, retain the same path. The API token remains server-side.
5. Open the port **8000** web preview in Safari. Flask serves the built dashboard and its API on the same origin. Sign in with username `reviewer` and your reviewer password. Keep the Replit project/preview private and use HTTPS. Do not publish an unauthenticated dashboard.
6. Refresh the page after restarting. Choose **Run read-only scan**. Alternatively, `python -m backend.daily_review` runs the same scan without any approvals or CRM writes.
7. Inspect the resulting account count and categories. The observed baseline was 35 website communities and 121 CRM records; counts may legitimately change. Old undecided inaccurate proposals become `superseded`, not deleted. The pending filter shows current proposals. Never approve from an old browser tab.

For frontend development only: run Flask on 8000 and `PORT=5173 BASE_PATH=/ pnpm --filter @workspace/bellhaven-crm-review dev` in a second shell. Vite proxies `/api/bellhaven` to Flask. The unrelated Express scaffold is not the CRM backend. For Basic auth, open `/api/bellhaven/review/health` in the same preview origin first if Safari does not show a login prompt for API requests.

If pnpm asks about a trusted esbuild build script, use its package approval flow for esbuild only, then rebuild. Do not enable all dependency scripts blindly.

## Matching and review

- Crawl homepage links (including Findlay), all directory pages, and each detail page. Extract name, address, city, state, ZIP, care offerings, and source URL. Any failed required detail aborts the scan.
- Normalize punctuation, legal suffixes, street abbreviations and cardinal directions. Compare five-digit ZIPs even with ZIP+4. Names use deterministic token/sequence similarity.
- An exact street/city/state match anchors renamed facilities at 90–100% confidence. The baseline score weights name 55%, ZIP 18%, city 12%, state 5%, street 10%. Missing street evidence or conflicting cities cannot qualify as a strong match; different house numbers or states cap confidence further.
- Strong matches start at 78%; plausible candidates at 48% enter manual investigation. Conflicting physical locations are ambiguous, even with strong name scores. Evidence lists candidate IDs and scores. Correct existing records produce no unnecessary proposal.
- Duplicate proposals require the same normalized physical address. Active status, score and ID deterministically suggest a survivor; the reviewer must confirm it. Approval marks the loser Inactive, sets `duplicate_of_account`, and appends an explanation without erasing notes. No merge/delete exists.
- Unlisted Bellhaven children are proposed for **Needs Review**, not automatically declared sold or inactive. The website's absence alone cannot prove ownership changed.
- Name/address changes and ownership corrections are separate approvals. Reconcile again after each change to refresh snapshots. Ambiguous matches can be rejected or investigated; they cannot execute CRM writes.
- A unique active, nonduplicate, top-level corporate account named Bellhaven Senior Living (with optional Parent Account suffix) is required for creation/reparenting. Its current unique ID is reverified at approval.

## Financial safeguards and write verification

| Revenue | Outstanding AR | Approved ownership operation |
| --- | --- | --- |
| 0 | 0 | Direct re-parent |
| Positive | 0 | Direct re-parent |
| 0 | Positive | Direct re-parent |
| Positive | Positive | Create successor, then link old `chow_current_account` |
| Missing, unparseable, negative or nonfinite | Any | Block and investigate |

Both financial fields are parsed independently; a zero in one never hides an unknown in the other. Approval fetches the current facility, checks identity/approved fields, rereads financial data, and rejects financial drift. CHOW preserves the original parent and billing history. The successor's complete approved payload is independently fetched and verified **before** linking the old account; the linkage and unchanged original parent are then verified too. An existing valid successor link is reused, never replaced with another creation.

POST creation uses a stable marker in the free-text API `note` field. The local result ID and creation uncertainty are persisted. If creation succeeds but linking fails, retry finds and verifies that successor. If a POST times out, a retry searches the marker; absent a visible marker it blocks instead of blindly posting again. A successful HTTP response alone never means the CRM change was applied: GET readback must agree. HTTP errors and verification failures appear in review history as failed, while approval remains recorded.

Only allowlisted fields map to documented POST/PATCH operations. Decisions require `BELLHAVEN_REVIEW_PASSWORD`; Basic authentication protects all routes when configured. JSON-only mutation routes, same-origin checks and no CORS block browser form/cross-origin CSRF. Use private HTTPS previews; Basic auth is a deliberately small assessment solution, not organization-wide identity management.

## Persistence, idempotency and recovery

`BELLHAVEN_DB_PATH` defaults to `backend/data/review.sqlite3`. SQLite contains communities, proposals, immutable reviewer decisions, write state/result IDs and append-only `audit_events`. Back it up privately. Never commit SQLite, `.env`, or backup files.

Proposal IDs include action, community/account and the reviewed facts. Identical scans preserve approvals/rejections. Material fact changes can create a new review; older undecided proposals become superseded. Historical legacy decisions are conservatively preserved for the same logical action. Applied operations cannot be submitted twice. An atomic database claim serializes writes across proposals/workers. Scans never approve anything.

A process crash during `applying` intentionally blocks further writes. **Do not delete the database or reset the row to pending.** Stop all app workers, inspect the proposal, its `audit_events`, current CRM state and creation marker. After confirming no worker remains, use a private Replit Shell to release the claim as failed while preserving any creation uncertainty:

```bash
# Run from artifacts/bellhaven-crm-review; replace only the proposal ID.
python - <<'PY'
from backend import store
proposal_id = 'REPLACE_WITH_REVIEWED_PROPOSAL_ID'
p = store.get_proposal(proposal_id)
assert p and p['crm_write_status'] == 'applying'
store.set_write_status(proposal_id, 'failed', p['crm_write_error'] or 'Interrupted write; CRM state inspected before retry.')
PY
```

Restart and explicitly retry the approved proposal. A known successor result ID/marker will be verified before linking. An unknown creation outcome without a marker remains blocked; investigate manually rather than clearing its error to force creation. No general decision deletion or automatic reopening is provided.

## Daily schedule

`examples/daily-review.cron` targets a persistent machine with the same database and environment as the dashboard. `examples/daily-review.example.yml` is an **inactive** GitHub Actions example requiring a private self-hosted runner and absolute durable `BELLHAVEN_DB_PATH` outside checkout. Do not change it to `ubuntu-latest` without implementing durable database synchronization: temporary Actions runners otherwise lose all decisions. SQLite must be local to a single persistent host (not a network share). Replit free previews may sleep and do not guarantee daily scheduling. Neither example calls an approval endpoint or writes CRM records.

## Validation and remaining live checks

```bash
# From artifacts/bellhaven-crm-review
python -m unittest discover -s backend/tests -v
# From repository root
pnpm run typecheck:libs
pnpm --filter @workspace/bellhaven-crm-review typecheck
PORT=5173 BASE_PATH=/ pnpm --filter @workspace/bellhaven-crm-review build
```

Repair validation: **57 backend tests passed, 0 failed**; shared TypeScript references and frontend typechecking passed; production Vite build passed; Flask dashboard, communities, activity and health routes returned HTTP 200 in a local smoke check.

Tests use synthetic fixture records, local HTML and a stateful fake CRM; no automated test can call a live CRM mutation. Tests cover the field regression, parent ambiguity, renamed facilities, physical conflicts, duplicate/stale handling, cases A–G, malformed responses, pagination/timeouts, verification failures, partial creation/link retries, durable rejection, atomic claims and CSRF/auth checks. The public read-only scrape was separately verified: **35 communities, Findlay included**. The public OpenAPI confirms GET/POST/PATCH routes but leaves account request/response schemas empty.

No assessment API token is configured in this Codex environment. Authenticated account counts, actual proposal categories, live POST/PATCH compatibility, Safari/Replit authentication and actual CRM corrections still require validation in Replit. Do not claim the CRM is corrected until approved writes are independently confirmed there. See [WALKTHROUGH.md](WALKTHROUGH.md) for the approval checklist and two-minute recording guide, and [SUBMISSION.md](SUBMISSION.md) for the concise writeup.

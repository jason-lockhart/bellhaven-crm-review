# Bellhaven CRM Review

Bellhaven CRM Review compares the Senior Living community directory with CRM accounts, explains each proposed change, and records reviewer decisions in SQLite. The initial pipeline is read-only: it scrapes the website, reads CRM accounts, and stages proposals. CRM writes happen only after a reviewer approves a proposal in the dashboard.

## Run it

1. Start the Replit preview and open the app.
2. Choose **Run read-only scan** to fetch the public website directory. This works without a CRM token.
3. Add `CLIPBOARD_API_TOKEN` through Replit Secrets. The value is read only by the Flask service and is never sent to the browser or written to the database.
4. Run the scan again to compare the directory with CRM accounts.
5. Review the evidence and approve or reject proposals individually. Rejected proposals do not make CRM requests.

## Access before publishing

The assessment brief did not specify sign-in, so this build does not include reviewer authentication. Keep the Replit project private. Before publishing it publicly, add reviewer sign-in and protect both the dashboard data and `/api/bellhaven` API routes.

## Data sources and API contract

- Website: `https://analyst-assessment-production.up.railway.app`
- CRM API: `https://analyst-assessment-production.up.railway.app/api/v1`
- The CRM bearer token is loaded from the `CLIPBOARD_API_TOKEN` environment variable.
- The live Swagger/OpenAPI page is `/api/docs`; its OpenAPI JSON is `/api/openapi.json`.
- The live account list exposes `account_id`, `billing_street`, `billing_city`, `billing_state`, `billing_zip`, and singular `note`; the adapter maps these to internal matcher fields. Other assessment fields include `parent_id`, `lifetime_revenue`, `outstanding_ar`, `chow_current_account`, `status`, and `duplicate_of_account`.
- The OpenAPI document leaves successful account response and request-body schemas empty. Reads were validated against the live account list, but write payloads have not been tested with a real approval. The adapter maps internal address and note fields back to the live API names, and only sends writes after explicit approval.

## Matching logic

- Follows the homepage's community links, every directory pagination link, and each discovered location's detail page.
- Compares normalized names first, then uses street address, city, state, and ZIP as supporting evidence.
- Uses a conservative confidence threshold. Weak or near-tied candidates are marked for manual review; they are not silently auto-matched.
- Proposes address corrections and parent changes only when the account match is strong and a unique top-level “Bellhaven Senior Living” parent account is present.
- Flags unmatched CRM child accounts under the identified parent as inactive candidates; it never deletes accounts.
- For duplicate candidates, the proposed canonical record is selected by active status, lifetime revenue, and confidence, with evidence attached for review.

## Parent change with revenue and receivables

When both `lifetime_revenue > 0` and `outstanding_ar > 0`, approval creates a new account under the correct parent and updates `chow_current_account` on the original record to the new account ID. The original account is preserved. If either value is zero or missing, approval updates the existing account's parent directly.

All approved writes are made through the documented `POST /api/v1/accounts` and `PATCH /api/v1/accounts/{account_id}` routes. The app saves approval state before making an external request. Created accounts carry a stable proposal marker in `notes` so a retry can find an already-created successor instead of creating another. Account deletion is not implemented.

## Local state and repeat runs

- `backend/data/review.sqlite3` stores scraped locations, proposals, approval/rejection decisions, and CRM write progress.
- Proposal IDs are stable for the same location, account, and proposed field changes.
- Re-running the read-only pipeline inserts only unseen proposals and never calls CRM write endpoints.
- The SQLite file is ignored by Git. Back it up if you need to preserve decisions outside this Replit project.

## Tests

Run the pure scraper and matching tests:

```bash
python -m unittest discover -s backend/tests -v
```

The tests use local HTML fixtures and do not require `CLIPBOARD_API_TOKEN` or write to the external CRM.

## Daily schedule example

`examples/daily-review.example.yml` is an inert GitHub Actions example. It stages proposals by running the read-only pipeline and does not approve or apply CRM changes. Copy it into `.github/workflows/` only if you want to enable a daily scan, and add the CRM token as a GitHub Actions secret.

## How AI was used

AI assisted with the initial application structure, implementation, and local test cases. Matching confidence is deterministic and based on explicit name and address evidence; AI does not decide or apply CRM changes.

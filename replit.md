# Bellhaven CRM Review

A review dashboard for comparing Bellhaven Senior Living communities with CRM accounts. Website and CRM data are reconciled into evidence-backed proposals; CRM changes require an explicit reviewer decision.

## Run & Operate

- Start the **Bellhaven CRM Review** web and Flask API workflows from the Replit workspace.
- `pnpm run typecheck` — full typecheck across all packages
- `cd artifacts/bellhaven-crm-review && python -m unittest discover -s backend/tests -v` — run scraper, matching, persistence, and approval-rule tests
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from the OpenAPI spec
- Required secret: `CLIPBOARD_API_TOKEN` — stored in Replit Secrets and read only by Flask

## Stack

- React + Vite dashboard; Flask API service
- Requests + BeautifulSoup for website scraping and CRM API reads
- SQLite for scraped locations, proposal decisions, and CRM write progress
- pnpm workspaces, TypeScript 5.9, Orval-generated review API hooks

## Where things live

- `artifacts/bellhaven-crm-review/src/` — dashboard and theme
- `artifacts/bellhaven-crm-review/backend/` — scraper, matcher, CRM client, Flask routes, SQLite store, and tests
- `artifacts/bellhaven-crm-review/README.md` — setup, API assumptions, matching rules, and schedule example
- `lib/api-spec/openapi.yaml` — review API contract; generated TypeScript hooks live in `lib/api-client-react/`

## Architecture decisions

- The Flask service is routed at `/api/bellhaven`, which is more specific than the shared API service's `/api` path.
- Reconciliation runs only scrape and read data; each CRM write is triggered by an explicit approval.
- Proposal IDs are deterministic and SQLite stores decisions so reruns preserve completed reviews.
- Parent changes preserve accounts with both positive lifetime revenue and outstanding A/R by creating a successor and linking `chow_current_account`.

## Product

The dashboard reviews source communities, proposed CRM updates, confidence, evidence, and prior decisions. The pipeline also flags duplicate, stale, unmatched, and ambiguous records without deleting accounts.

## User preferences

- Keep the review interface usable from an iPad browser.

## Gotchas

- Add `CLIPBOARD_API_TOKEN` through Replit Secrets before reading CRM accounts.
- The published CRM OpenAPI document leaves account request and response schemas blank; verify live records before changing field mappings.
- Authentication is not part of the current assessment scope. Keep this project private; do not publish until reviewer access is protected.

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details

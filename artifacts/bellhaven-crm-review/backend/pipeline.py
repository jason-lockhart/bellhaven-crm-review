from __future__ import annotations

from . import crm_client, matching, scraper, store


def run_reconciliation() -> dict:
    """Scrape and stage proposals. This function never sends a CRM write."""
    communities = scraper.scrape_communities()
    store.save_communities(communities)

    if not crm_client.token_is_configured():
        return {
            "status": "crm_not_configured",
            "communities_scanned": len(communities),
            "accounts_scanned": 0,
            "proposals_created": 0,
            "proposals_existing": 0,
            "warnings": [
                "The website scrape completed. Add CLIPBOARD_API_TOKEN as a Replit secret, then run the read-only scan again to compare CRM accounts."
            ],
        }

    client = crm_client.CRMClient()
    accounts = client.list_accounts()
    proposals = matching.build_proposals(communities, accounts)
    created, existing = store.upsert_proposals(proposals)
    return {
        "status": "completed",
        "communities_scanned": len(communities),
        "accounts_scanned": len(accounts),
        "proposals_created": created,
        "proposals_existing": existing,
        "warnings": [],
    }

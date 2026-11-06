from __future__ import annotations

import os
import logging
from pathlib import Path
import sys

from flask import Flask, jsonify, request

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from backend import crm_client, matching, pipeline, store
    from backend.crm_client import CRMClient, CRMError
else:
    from . import crm_client, matching, pipeline, store
    from .crm_client import CRMClient, CRMError

API_PREFIX = "/api/bellhaven/review"
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024
store.initialize()


def _json_error(message: str, status: int):
    return jsonify({"error": message}), status


def _account_index(client: CRMClient) -> dict[str, dict]:
    return {
        str(account["id"]): account
        for account in client.list_accounts()
        if account.get("id") is not None
    }


def _write_marker(proposal_id: str) -> str:
    return f"Bellhaven CRM Review proposal: {proposal_id}"


def _find_created_account(accounts: dict[str, dict], proposal_id: str) -> dict | None:
    marker = _write_marker(proposal_id)
    for account in accounts.values():
        if marker in str(account.get("notes") or ""):
            return account
    return None


def _create_payload(proposal: dict) -> dict:
    community = proposal.get("community") or {}
    original = proposal.get("account") or {}
    is_successor = proposal["type"] == "parent_change"
    parent_id = next(
        (
            change.get("after")
            for change in proposal.get("changes", [])
            if change.get("field") == "parent_id"
        ),
        None,
    )
    if not parent_id:
        raise CRMError("No unique Bellhaven parent account was found; an account was not created.")
    name = original.get("name") if is_successor else community.get("name")
    street = community.get("address") if community else original.get("street")
    city = community.get("city") if community else original.get("city")
    state = community.get("state") if community else original.get("state")
    postal_code = community.get("zip") if community else original.get("zip")
    note_lines = [str(original.get("notes") or "").strip()] if is_successor else []
    note_lines.append(_write_marker(proposal["id"]))
    if is_successor:
        note_lines.append(f"Successor account for {original.get('id')} created after reviewer approval.")
    else:
        note_lines.append("Created after reviewer approval from the Bellhaven community directory.")
    return {
        "name": str(name or "").strip(),
        "street": str(street or "").strip(),
        "city": str(city or "").strip(),
        "state": str(state or "").strip(),
        "zip": str(postal_code or "").strip(),
        "parent_id": str(parent_id),
        "status": str(original.get("status") or "Active") if is_successor else "Active",
        "notes": "\n".join(line for line in note_lines if line),
    }


def _apply_approved_proposal(proposal: dict) -> str | None:
    client = CRMClient()
    account = proposal.get("account") or {}
    proposal_type = proposal["type"]
    account_id = str(account.get("id") or "")

    needs_successor = proposal_type == "parent_change" and matching.requires_successor_account(account)

    if proposal_type == "missing_account" or needs_successor:
        accounts = _account_index(client)
        created = _find_created_account(accounts, proposal["id"])
        if created is None:
            created = client.create_account(_create_payload(proposal))
            result_id = str(created["id"])
            store.set_write_status(proposal["id"], "created", result_account_id=result_id)
        else:
            result_id = str(created["id"])

        if proposal_type == "parent_change":
            old_id = str(account.get("id") or "")
            if not old_id:
                raise CRMError("The original CRM account identifier is missing.")
            if str(account.get("chow_current_account") or "") != result_id:
                client.update_account(old_id, {"chow_current_account": result_id})
        return result_id

    if not account_id:
        raise CRMError("The approved proposal does not include a CRM account identifier.")

    updates = {
        str(change["field"]): change["after"]
        for change in proposal.get("changes", [])
        if change.get("field") and change.get("field") != "account"
    }
    if not updates:
        raise CRMError("This proposal has no supported CRM field changes.")
    client.update_account(account_id, updates)
    return account_id


@app.get(f"{API_PREFIX}/health")
def review_health():
    has_website_snapshot = bool(store.list_communities())
    return jsonify(
        {
            "status": "ready" if crm_client.token_is_configured() else "needs_setup",
            "website_available": has_website_snapshot,
            "crm_configured": crm_client.token_is_configured(),
            "read_only": True,
            "last_run_at": store.summary(0, crm_client.token_is_configured())["last_run_at"],
        }
    )


@app.get(f"{API_PREFIX}/summary")
def review_summary():
    total_accounts = 0
    if crm_client.token_is_configured():
        try:
            total_accounts = len(CRMClient().list_accounts())
        except CRMError:
            total_accounts = 0
    return jsonify(store.summary(total_accounts, crm_client.token_is_configured()))


@app.get(f"{API_PREFIX}/communities")
def communities():
    return jsonify(store.list_communities())


@app.get(f"{API_PREFIX}/proposals")
def proposals():
    status = request.args.get("status") or None
    proposal_type = request.args.get("type") or None
    search = request.args.get("search") or None
    try:
        page = max(1, int(request.args.get("page", "1")))
        page_size = min(100, max(1, int(request.args.get("page_size", "25"))))
    except ValueError:
        return _json_error("page and page_size must be integers.", 400)
    if status not in {None, "pending", "approved", "rejected"}:
        return _json_error("Unsupported proposal status.", 400)
    items, total = store.list_proposals(status, proposal_type, search, page, page_size)
    return jsonify({"items": items, "total": total, "page": page, "page_size": page_size})


@app.get(f"{API_PREFIX}/activity")
def activity():
    return jsonify(store.recent_activity())


@app.post(f"{API_PREFIX}/run")
def run_review():
    try:
        result = pipeline.run_reconciliation()
        return jsonify(result)
    except scraper.ScrapeError as exc:
        return _json_error(str(exc), 502)
    except CRMError as exc:
        return _json_error(str(exc), 502)
    except Exception:
        app.logger.exception("Unexpected reconciliation error")
        return _json_error("The reconciliation did not complete. Check the server log for details.", 500)


@app.post(f"{API_PREFIX}/proposals/<proposal_id>/decision")
def decide_proposal(proposal_id: str):
    payload = request.get_json(silent=True) or {}
    decision = payload.get("decision")
    if decision not in {"approved", "rejected"}:
        return _json_error("decision must be approved or rejected.", 400)
    reviewer_notes = payload.get("reviewer_notes")
    if reviewer_notes is not None and not isinstance(reviewer_notes, str):
        return _json_error("reviewer_notes must be text.", 400)

    proposal = store.get_proposal(proposal_id)
    if not proposal:
        return _json_error("Proposal not found.", 404)
    if proposal["status"] in {"approved", "rejected"} and proposal["status"] != decision:
        return _json_error("This proposal already has a different recorded decision.", 409)
    if proposal["status"] == "rejected":
        return jsonify(
            {"proposal": proposal, "message": "This rejection was already recorded.", "crm_updated": False}
        )
    if proposal["status"] == "approved" and proposal.get("crm_write_status") == "applied":
        return jsonify(
            {"proposal": proposal, "message": "This approved CRM change was already applied.", "crm_updated": True}
        )

    if decision == "rejected":
        saved = store.set_decision(
            proposal_id,
            "rejected",
            reviewer_notes,
            write_status="not_applicable",
        )
        return jsonify(
            {"proposal": saved, "message": "Rejection recorded. No CRM data was changed.", "crm_updated": False}
        )

    # Persist the reviewer's decision before attempting an external write.
    saved = store.set_decision(
        proposal_id,
        "approved",
        reviewer_notes,
        write_status="waiting_for_token" if not crm_client.token_is_configured() else "applying",
    )
    if not crm_client.token_is_configured():
        return jsonify(
            {
                "proposal": saved,
                "message": "Approval recorded. Add CLIPBOARD_API_TOKEN, then explicitly retry this approved change.",
                "crm_updated": False,
            }
        )

    try:
        result_account_id = _apply_approved_proposal(saved)
        saved = store.set_write_status(
            proposal_id,
            "applied",
            result_account_id=result_account_id,
        )
        return jsonify(
            {"proposal": saved, "message": "Approved CRM change applied.", "crm_updated": True}
        )
    except CRMError as exc:
        saved = store.set_write_status(proposal_id, "failed", str(exc))
        return jsonify(
            {
                "proposal": saved,
                "message": f"Approval was saved, but the CRM update failed: {exc}",
                "crm_updated": False,
            }
        )
    except Exception:
        app.logger.exception("Unexpected CRM write error for approved proposal %s", proposal_id)
        saved = store.set_write_status(
            proposal_id,
            "failed",
            "Unexpected CRM write failure. Retry only after checking the CRM for a partial update.",
        )
        return jsonify(
            {
                "proposal": saved,
                "message": "Approval was saved, but the CRM update failed. Check CRM state before retrying.",
                "crm_updated": False,
            }
        )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=False)

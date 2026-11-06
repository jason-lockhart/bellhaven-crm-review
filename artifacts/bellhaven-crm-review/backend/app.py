from __future__ import annotations

import os
import secrets
from urllib.parse import urlparse
from pathlib import Path
import sys

from flask import Flask, jsonify, request

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from backend import crm_client, matching, pipeline, store, scraper
    from backend.crm_client import CRMClient, CRMError
else:
    from . import crm_client, matching, pipeline, store, scraper
    from .crm_client import CRMClient, CRMError

API_PREFIX = "/api/bellhaven/review"
app = Flask(__name__, static_folder="../dist/public", static_url_path="")
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


def _verify(client: CRMClient, account_id: str, fields: dict) -> dict:
    current = client.get_account(account_id)
    for field, value in fields.items():
        if current.get(field) != value:
            raise CRMError(f"CRM verification failed for field {field}; investigate before retrying.")
    return current


def _apply_approved_proposal(proposal: dict) -> str | None:
    if proposal.get("matcher_version") != matching.MATCHER_VERSION:
        raise CRMError("Legacy proposal must be reconciled again before approval.")
    if proposal["type"] == "ambiguous_match":
        raise CRMError("Investigate the candidates and reconcile again; ambiguous matches cannot write.")
    client = CRMClient()
    snapshot = proposal.get("account") or {}
    account_id = str(snapshot.get("id") or "")
    current = client.get_account(account_id) if account_id else None
    updates = {c["field"]: c["after"] for c in proposal.get("changes", [])}
    parent_id = updates.get("parent_id")
    accounts = _account_index(client)
    if proposal["type"] in {"parent_change", "missing_account"}:
        parent = matching.identify_parent(list(accounts.values()))
        if not parent or str(parent["id"]) != str(parent_id):
            raise CRMError("The approved corporate parent is no longer uniquely established.")
    if current:
        for field in ("name", "street", "city", "state", "zip"):
            approved_after = updates.get(field, snapshot.get(field))
            if current.get(field, "") not in (snapshot.get(field, ""), approved_after):
                raise CRMError("Facility identity changed since review; reconcile again.")
        for change in proposal.get("changes", []):
            field = change["field"]
            if current.get(field) not in (change.get("before"), change.get("after")):
                raise CRMError("CRM fields changed since review; reconcile again.")
    needs_successor = False
    if proposal["type"] == "parent_change":
        try:
            needs_successor = matching.requires_successor_account(current)
            previous_rule = matching.requires_successor_account(snapshot)
        except ValueError as exc:
            raise CRMError(str(exc)) from None
        if needs_successor != previous_rule or any(matching.parse_financial_value(current.get(f)) != matching.parse_financial_value(snapshot.get(f)) for f in ("lifetime_revenue", "outstanding_ar")):
            raise CRMError("Financial safeguards changed since review; reconcile again.")
        # Existing linkage is authoritative, provided it really identifies this facility.
        linked_id = current.get("chow_current_account")
        if linked_id:
            payload = _create_payload(proposal)
            _verify(client, str(linked_id), {k: v for k, v in payload.items() if k in {"parent_id", "street", "city", "state", "zip"}})
            if str(linked_id) == account_id:
                raise CRMError("Invalid self-referencing CHOW linkage.")
            return str(linked_id)
    if proposal["type"] == "missing_account" or needs_successor:
        payload = _create_payload(proposal)
        matches = [a for a in accounts.values() if _write_marker(proposal["id"]) in str(a.get("notes") or "")]
        if len(matches) > 1:
            raise CRMError("Multiple creation markers found; investigate manually.")
        created = matches[0] if matches else None
        known_id = proposal.get("crm_result_account_id")
        if created is None and known_id:
            created = client.get_account(str(known_id))
        if created is None:
            # Never create after a timed-out POST with an unknown result.
            if proposal.get("crm_write_error", "") and "unknown" in proposal["crm_write_error"]:
                raise CRMError("Creation outcome unknown; inspect the CRM before retrying.")
            if proposal["type"] == "missing_account" and any(matching.score_account(proposal["community"], a)[0] >= .48 for a in accounts.values() if str(a["id"]) != str(parent_id)):
                raise CRMError("A possible facility now exists; reconcile before creating.")
            store.set_write_status(proposal["id"], "applying", "Creation outcome unknown until independently verified.")
            try:
                created = client.create_account(payload)
            except CRMError:
                raise CRMError("Creation outcome unknown; inspect the CRM and retry only after locating its marker.") from None
            store.set_write_status(proposal["id"], "applying", result_account_id=str(created["id"]))
        result_id = str(created["id"])
        _verify(client, result_id, payload)
        if needs_successor:
            # The original parent must remain unchanged throughout CHOW.
            original = client.get_account(account_id)
            if original.get("parent_id") != current.get("parent_id") or original.get("chow_current_account") not in (None, "", result_id):
                raise CRMError("Original account changed during CHOW; successor preserved for investigation.")
            client.update_account(account_id, {"chow_current_account": result_id})
            _verify(client, account_id, {"chow_current_account": result_id, "parent_id": current.get("parent_id")})
        return result_id
    if not current or not updates:
        raise CRMError("No verified account or approved field changes.")
    if proposal["type"] == "duplicate_account":
        survivor_id = str(updates["duplicate_of_account"])
        survivor = client.get_account(survivor_id)
        if survivor_id == account_id or matching.score_account(proposal["community"], survivor)[0] < .78:
            raise CRMError("Duplicate survivor is not a verified facility match.")
        existing_notes = str(current.get("notes") or "")
        marker = _write_marker(proposal["id"])
        updates["notes"] = existing_notes if marker in existing_notes else (
            existing_notes + "\n" + marker + f"; duplicate of {survivor_id}; billing history preserved."
        ).strip()
    if any(current.get(k) != v for k, v in updates.items()):
        client.update_account(account_id, updates)
    _verify(client, account_id, updates)
    return account_id


@app.get("/")
def dashboard():
    return app.send_static_file("index.html")


@app.errorhandler(404)
def frontend_route(error):
    if request.method == "GET" and request.path in {"/activity", "/communities"}:
        return app.send_static_file("index.html")
    return _json_error("Not found.", 404)


@app.before_request
def protect_browser_writes():
    password = os.environ.get("BELLHAVEN_REVIEW_PASSWORD", "")
    if password:
        auth = request.authorization
        if not auth or not secrets.compare_digest(auth.password or "", password):
            return jsonify({"error": "Reviewer authentication required."}), 401, {"WWW-Authenticate": 'Basic realm="Bellhaven review"'}
    elif request.method == "POST" and request.path.endswith("/decision") and not app.config.get("TESTING"):
        return _json_error("Configure BELLHAVEN_REVIEW_PASSWORD before recording decisions.", 503)

    if request.method != "POST":
        return None
    # JSON-only + no CORS defeats form CSRF. Cross-origin fetches are refused.
    if not request.is_json:
        return _json_error("JSON requests are required.", 415)
    origin = request.headers.get("Origin")
    if request.headers.get("Sec-Fetch-Site") == "cross-site" or (origin and urlparse(origin).netloc != request.host):
        return _json_error("Cross-origin writes are forbidden.", 403)
    # Keep the dashboard behind Replit's private access controls.
    return None


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
    if decision == "approved" and (proposal.get("matcher_version") != matching.MATCHER_VERSION or proposal["type"] == "ambiguous_match"):
        return _json_error("This proposal requires investigation and a fresh reconciliation before approval.", 409)
    if proposal["status"] == "superseded":
        return _json_error("Proposal was superseded; run reconciliation and review the current evidence.", 409)
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
        if not store.record_rejection(proposal_id, reviewer_notes):
            return _json_error("The proposal changed during this decision. Refresh the review queue.", 409)
        saved = store.get_proposal(proposal_id)
        return jsonify(
            {"proposal": saved, "message": "Rejection recorded. No CRM data was changed.", "crm_updated": False}
        )

    if not store.claim_approval(proposal_id, reviewer_notes):
        return _json_error("A write is already running or completed. Inspect history before retrying.", 409)
    saved = store.get_proposal(proposal_id)
    if not crm_client.token_is_configured():
        saved = store.set_write_status(proposal_id, "waiting_for_token")
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

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from difflib import SequenceMatcher

LEGAL_SUFFIXES = {"inc", "llc", "ltd", "limited", "corp", "corporation", "company", "co"}
ADDRESS_WORDS = {
    "street": "st",
    "avenue": "ave",
    "road": "rd",
    "boulevard": "blvd",
    "drive": "dr",
    "lane": "ln",
    "court": "ct",
    "highway": "hwy",
    "parkway": "pkwy",
    "place": "pl",
    "terrace": "ter",
}


def normalize_name(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode()
    text = text.casefold().replace("&", " and ")
    tokens = re.findall(r"[a-z0-9]+", text)
    while tokens and tokens[-1] in LEGAL_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def normalize_address(value: object) -> str:
    text = normalize_name(value)
    tokens = [ADDRESS_WORDS.get(token, token) for token in text.split()]
    return " ".join(tokens)


def name_similarity(left: object, right: object) -> float:
    a, b = normalize_name(left), normalize_name(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    a_tokens, b_tokens = set(a.split()), set(b.split())
    overlap = (2 * len(a_tokens & b_tokens)) / max(1, len(a_tokens) + len(b_tokens))
    sequence = SequenceMatcher(None, a, b).ratio()
    return max(sequence, overlap)


def _text(account: dict, key: str) -> str:
    value = account.get(key)
    return "" if value is None else str(value).strip()


def score_account(community: dict, account: dict) -> tuple[float, list[str]]:
    """Score a candidate with explainable location evidence."""
    evidence: list[str] = []
    name_score = name_similarity(community.get("name"), account.get("name"))
    if name_score >= 0.999:
        evidence.append("Community name matches exactly after normalization.")
    elif name_score >= 0.65:
        evidence.append(f"Community name is similar ({name_score:.0%}).")

    weights: list[tuple[float, float]] = [(0.55, name_score)]
    website_zip = re.sub(r"\D", "", str(community.get("zip") or ""))
    account_zip = re.sub(r"\D", "", _text(account, "zip"))
    if website_zip and account_zip:
        score = 1.0 if website_zip == account_zip else 0.0
        weights.append((0.18, score))
        evidence.append("ZIP code matches." if score else "ZIP code differs.")
    else:
        evidence.append("ZIP code evidence is incomplete.")

    website_city = normalize_name(community.get("city"))
    account_city = normalize_name(account.get("city"))
    if website_city and account_city:
        score = 1.0 if website_city == account_city else 0.0
        weights.append((0.12, score))
        evidence.append("City matches." if score else "City differs.")
    else:
        evidence.append("City evidence is incomplete.")

    website_state = normalize_name(community.get("state"))
    account_state = normalize_name(account.get("state"))
    if website_state and account_state:
        score = 1.0 if website_state == account_state else 0.0
        weights.append((0.05, score))
        evidence.append("State matches." if score else "State differs.")
    else:
        evidence.append("State evidence is incomplete.")

    website_street = normalize_address(community.get("address"))
    account_street = normalize_address(account.get("street"))
    if website_street and account_street:
        score = 1.0 if website_street == account_street else SequenceMatcher(
            None, website_street, account_street
        ).ratio()
        weights.append((0.10, score))
        evidence.append("Street address matches." if score >= 0.98 else "Street address differs.")
    else:
        evidence.append("Street address evidence is incomplete.")

    # Missing fields reduce confidence instead of silently reweighting a name-only match.
    confidence = sum(weight * score for weight, score in weights)
    return round(confidence, 4), evidence


def _number(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def requires_successor_account(account: dict) -> bool:
    """Apply the assessment's two-condition change-of-ownership rule."""
    return _number(account.get("lifetime_revenue")) > 0 and _number(account.get("outstanding_ar")) > 0


def snapshot_account(account: dict, account_by_id: dict[str, dict]) -> dict:
    account_id = str(account.get("id") or "")
    parent_id_value = account.get("parent_id")
    parent_id = str(parent_id_value) if parent_id_value is not None else None
    parent = account_by_id.get(parent_id or "")
    return {
        "id": account_id,
        "name": str(account.get("name") or ""),
        "street": str(account.get("street") or ""),
        "city": str(account.get("city") or ""),
        "state": str(account.get("state") or ""),
        "zip": str(account.get("zip") or ""),
        "parent_id": parent_id,
        "parent_name": str(parent.get("name")) if parent and parent.get("name") else None,
        "lifetime_revenue": _number(account.get("lifetime_revenue")),
        "outstanding_ar": _number(account.get("outstanding_ar")),
        "chow_current_account": (
            str(account.get("chow_current_account"))
            if account.get("chow_current_account") is not None
            else None
        ),
        "status": str(account.get("status")) if account.get("status") is not None else None,
        "notes": str(account.get("notes")) if account.get("notes") is not None else None,
        "duplicate_of_account": (
            str(account.get("duplicate_of_account"))
            if account.get("duplicate_of_account") is not None
            else None
        ),
    }


def _proposal(
    proposal_type: str,
    title: str,
    summary: str,
    community: dict | None,
    account: dict | None,
    changes: list[dict],
    evidence: list[str],
    confidence: float,
    manual_review: bool = False,
) -> dict:
    identity = {
        "type": proposal_type,
        "slug": community.get("slug") if community else None,
        "account_id": account.get("id") if account else None,
        "changes": changes,
    }
    proposal_id = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:20]
    risk_level = "high" if proposal_type in {"parent_change", "missing_account"} else (
        "medium" if manual_review or proposal_type in {"duplicate_account", "stale_account"} else "low"
    )
    return {
        "id": proposal_id,
        "type": proposal_type,
        "status": "pending",
        "confidence": round(confidence, 4),
        "risk_level": risk_level,
        "title": title,
        "summary": summary,
        "community": community,
        "account": account,
        "changes": changes,
        "evidence": evidence,
        "manual_review": manual_review,
        "reviewer_notes": None,
        "created_at": None,
    }


def build_proposals(communities: list[dict], raw_accounts: list[dict]) -> list[dict]:
    """Build conservative, evidence-backed proposals without writing CRM records."""
    accounts_by_id = {
        str(account["id"]): account for account in raw_accounts if account.get("id") is not None
    }
    accounts = [
        account
        for account in raw_accounts
        if account.get("id") is not None and str(account.get("name") or "").strip()
    ]
    snapshots = {
        str(account["id"]): snapshot_account(account, accounts_by_id) for account in accounts
    }
    parent_candidates = [
        account
        for account in accounts
        if not account.get("parent_id")
        and normalize_name(account.get("name")).startswith("bellhaven senior living")
    ]
    expected_parent = parent_candidates[0] if len(parent_candidates) == 1 else None
    expected_parent_id = str(expected_parent["id"]) if expected_parent else None
    proposals: list[dict] = []
    matched_account_ids: set[str] = set()
    duplicate_account_ids: set[str] = set()

    for community in communities:
        candidates = []
        for account in accounts:
            if expected_parent and str(account["id"]) == expected_parent_id:
                continue
            confidence, evidence = score_account(community, account)
            candidates.append((confidence, account, evidence))
        candidates.sort(key=lambda item: item[0], reverse=True)

        strong = [item for item in candidates if item[0] >= 0.78]
        if len(strong) > 1:
            strong.sort(
                key=lambda item: (
                    item[1].get("status") == "Active",
                    _number(item[1].get("lifetime_revenue")),
                    item[0],
                ),
                reverse=True,
            )
            canonical = strong[0][1]
            matched_account_ids.add(str(canonical["id"]))
            canonical_snapshot = snapshots[str(canonical["id"])]
            for confidence, duplicate, evidence in strong[1:]:
                duplicate_id = str(duplicate["id"])
                duplicate_account_ids.add(duplicate_id)
                matched_account_ids.add(duplicate_id)
                snapshot = snapshots[duplicate_id]
                changes = [
                    {"field": "status", "before": snapshot["status"], "after": "Inactive"},
                    {
                        "field": "duplicate_of_account",
                        "before": snapshot["duplicate_of_account"],
                        "after": str(canonical["id"]),
                    },
                ]
                proposals.append(
                    _proposal(
                        "duplicate_account",
                        f"Mark duplicate record for {community['name']}",
                        f"Several CRM accounts strongly match this location. Keep {canonical_snapshot['name']} as the primary record and relate this account to it.",
                        community,
                        snapshot,
                        changes,
                        evidence
                        + [f"Another high-confidence CRM account also matches: {canonical_snapshot['name']}."],
                        confidence,
                        manual_review=True,
                    )
                )
            continue

        if candidates and candidates[0][0] >= 0.78:
            confidence, account, evidence = candidates[0]
            account_id = str(account["id"])
            matched_account_ids.add(account_id)
            snapshot = snapshots[account_id]
            address_changes = []
            for field, website_field in [
                ("street", "address"),
                ("city", "city"),
                ("state", "state"),
                ("zip", "zip"),
            ]:
                before = snapshot[field]
                after = str(community.get(website_field) or "")
                if after and normalize_address(before) != normalize_address(after):
                    address_changes.append({"field": field, "before": before, "after": after})
            if address_changes:
                proposals.append(
                    _proposal(
                        "address_update",
                        f"Update location details for {community['name']}",
                        "The website and CRM have different address details.",
                        community,
                        snapshot,
                        address_changes,
                        evidence
                        + [
                            f"Website address: {community['address']}, {community['city']}, {community['state']} {community['zip']}."
                        ],
                        confidence,
                    )
                )
            if expected_parent_id and snapshot["parent_id"] != expected_parent_id:
                parent_change = [
                    {
                        "field": "parent_id",
                        "before": snapshot["parent_id"],
                        "after": expected_parent_id,
                    }
                ]
                proposals.append(
                    _proposal(
                        "parent_change",
                        f"Correct the parent for {community['name']}",
                        f"Move this location under {expected_parent.get('name')}.",
                        community,
                        snapshot,
                        parent_change,
                        evidence
                        + [
                            f"The Bellhaven parent account was identified as {expected_parent.get('name')} ({expected_parent_id})."
                        ],
                        confidence,
                    )
                )
            continue

        if candidates and candidates[0][0] >= 0.48:
            confidence, account, evidence = candidates[0]
            snapshot = snapshots[str(account["id"])]
            matched_account_ids.add(str(account["id"]))
            proposals.append(
                _proposal(
                    "ambiguous_match",
                    f"Review possible match for {community['name']}",
                    "The available name and location evidence is not strong enough to recommend an automatic match.",
                    community,
                    snapshot,
                    [
                        {
                            "field": "status",
                            "before": snapshot["status"],
                            "after": "Needs Review",
                        }
                    ],
                    evidence or ["No reliable matching evidence was found."],
                    confidence,
                    manual_review=True,
                )
            )
            continue

        parent_change = [
            {
                "field": "parent_id",
                "before": None,
                "after": expected_parent_id,
            }
        ]
        proposals.append(
            _proposal(
                "missing_account",
                f"Create a CRM account for {community['name']}",
                "This website location did not have a strong CRM match.",
                community,
                None,
                parent_change,
                [
                    "No CRM account met the conservative name-and-location match threshold.",
                    f"Website location: {community['address']}, {community['city']}, {community['state']} {community['zip']}.",
                    (
                        f"Correct parent account found: {expected_parent.get('name')}."
                        if expected_parent
                        else "No unique Bellhaven parent account was found in the CRM."
                    ),
                ],
                candidates[0][0] if candidates else 0.0,
                manual_review=expected_parent is None,
            )
        )

    if expected_parent_id:
        for account in accounts:
            account_id = str(account["id"])
            snapshot = snapshots[account_id]
            if (
                account_id == expected_parent_id
                or snapshot["parent_id"] != expected_parent_id
                or account_id in matched_account_ids
                or account_id in duplicate_account_ids
            ):
                continue
            if snapshot["status"] == "Inactive":
                continue
            proposals.append(
                _proposal(
                    "stale_account",
                    f"Review CRM location no longer listed: {snapshot['name']}",
                    "This CRM child account did not match any location found on the Bellhaven website.",
                    None,
                    snapshot,
                    [{"field": "status", "before": snapshot["status"], "after": "Inactive"}],
                    [
                        f"Account is a child of the identified Bellhaven parent ({expected_parent_id}).",
                        "No community with a strong name-and-location match was found across all scraped directory pages.",
                    ],
                    0.5,
                    manual_review=True,
                )
            )

    return proposals

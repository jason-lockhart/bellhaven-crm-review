from __future__ import annotations

import os
from typing import Any

import requests

CRM_API_ROOT = "https://analyst-assessment-production.up.railway.app/api/v1"
PAGE_SIZE = 50
MAX_PAGES = 100
ACCOUNT_FIELD_ALIASES = {
    "id": ("id", "account_id"),
    "street": ("street", "billing_street"),
    "city": ("city", "billing_city"),
    "state": ("state", "billing_state"),
    "zip": ("zip", "billing_zip"),
    "notes": ("notes", "note"),
}
API_FIELD_ALIASES = {
    "street": "billing_street",
    "city": "billing_city",
    "state": "billing_state",
    "zip": "billing_zip",
    "notes": "note",
}
WRITABLE_API_FIELDS = {
    "name",
    "billing_street",
    "billing_city",
    "billing_state",
    "billing_zip",
    "parent_id",
    "status",
    "note",
    "duplicate_of_account",
    "chow_current_account",
}
ACCOUNT_STATUSES = {"Active", "Inactive", "Needs Review"}


class CRMError(RuntimeError):
    """A sanitized CRM API failure; never includes request authorization headers."""


def normalize_account_record(record: dict) -> dict:
    """Map the live API's billing/account field names to matcher field names."""
    normalized = dict(record)
    for canonical, candidates in ACCOUNT_FIELD_ALIASES.items():
        if normalized.get(canonical) not in (None, ""):
            continue
        for candidate in candidates:
            value = record.get(candidate)
            if value not in (None, ""):
                normalized[canonical] = value
                break
    if normalized.get("id") is not None:
        normalized["id"] = str(normalized["id"])
    if normalized.get("parent_id") is not None:
        normalized["parent_id"] = str(normalized["parent_id"])
    return normalized


def _account_from_response(payload: Any) -> dict:
    if isinstance(payload, dict) and isinstance(payload.get("account"), dict):
        payload = payload["account"]
    elif isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        payload = payload["data"]
    if not isinstance(payload, dict):
        raise CRMError("The CRM account endpoint returned an unrecognized response.")
    return normalize_account_record(payload)


def _api_account_payload(payload: dict) -> dict:
    result = {}
    for field, value in payload.items():
        api_field = API_FIELD_ALIASES.get(field, field)
        if api_field not in WRITABLE_API_FIELDS:
            raise CRMError("The approved change contains an unsupported CRM field.")
        if api_field == "status" and value not in ACCOUNT_STATUSES:
            raise CRMError("The approved change contains an unsupported account status.")
        result[api_field] = value
    return result


def token_is_configured() -> bool:
    return bool(os.environ.get("CLIPBOARD_API_TOKEN", "").strip())


class CRMClient:
    def __init__(self, token: str | None = None, session: requests.Session | None = None):
        actual_token = token if token is not None else os.environ.get("CLIPBOARD_API_TOKEN", "")
        if not actual_token.strip():
            raise CRMError("CLIPBOARD_API_TOKEN is not configured.")
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {actual_token.strip()}",
                "Accept": "application/json",
                "User-Agent": "BellhavenCRMReview/1.0",
            }
        )

    @staticmethod
    def _request_error(response: requests.Response) -> CRMError:
        if response.status_code in (401, 403):
            return CRMError(
                f"CRM authentication failed with HTTP {response.status_code}. "
                "Check the CLIPBOARD_API_TOKEN secret."
            )
        return CRMError(f"CRM API request failed with HTTP {response.status_code}.")

    @staticmethod
    def _records_from_response(payload: Any) -> list[dict]:
        if isinstance(payload, list) and all(isinstance(row, dict) for row in payload):
            return payload
        if isinstance(payload, dict):
            lists = [
                value
                for value in payload.values()
                if isinstance(value, list) and all(isinstance(row, dict) for row in value)
            ]
            if len(lists) == 1:
                return lists[0]
            if not lists:
                return []
        raise CRMError(
            "The documented accounts endpoint returned an unrecognized response shape. "
            "The published OpenAPI document does not define its response schema."
        )

    def list_accounts(self) -> list[dict]:
        accounts: list[dict] = []
        seen_ids: set[str] = set()

        for page in range(1, MAX_PAGES + 1):
            try:
                response = self.session.get(
                    f"{CRM_API_ROOT}/accounts",
                    params={"page": page, "page_size": PAGE_SIZE},
                    timeout=25,
                )
            except requests.RequestException as exc:
                raise CRMError(f"Could not reach the CRM accounts endpoint: {exc}") from exc
            if not response.ok:
                raise self._request_error(response)
            try:
                raw_batch = self._records_from_response(response.json())
            except ValueError as exc:
                raise CRMError("The CRM accounts endpoint returned invalid JSON.") from exc
            if not raw_batch:
                break
            batch = [normalize_account_record(row) for row in raw_batch]
            for row in batch:
                account_id = row.get("id")
                if account_id is None:
                    raise CRMError("A CRM account record did not include an account identifier.")
                if account_id not in seen_ids:
                    accounts.append(row)
                    seen_ids.add(account_id)
            if len(batch) < PAGE_SIZE:
                break
        else:
            raise CRMError(f"CRM pagination exceeded the safety limit of {MAX_PAGES} pages.")

        return accounts

    def create_account(self, payload: dict) -> dict:
        try:
            response = self.session.post(
                f"{CRM_API_ROOT}/accounts",
                json=_api_account_payload(payload),
                timeout=25,
            )
        except requests.RequestException as exc:
            raise CRMError(f"Could not create the approved CRM account: {exc}") from exc
        if not response.ok:
            raise self._request_error(response)
        try:
            result = _account_from_response(response.json())
        except ValueError as exc:
            raise CRMError("The CRM create-account endpoint returned invalid JSON.") from exc
        if not isinstance(result, dict) or result.get("id") is None:
            raise CRMError(
                "The CRM create-account response did not include the documented account identifier."
            )
        return result

    def update_account(self, account_id: str, payload: dict) -> dict:
        try:
            response = self.session.patch(
                f"{CRM_API_ROOT}/accounts/{account_id}",
                json=_api_account_payload(payload),
                timeout=25,
            )
        except requests.RequestException as exc:
            raise CRMError(f"Could not update the approved CRM account: {exc}") from exc
        if not response.ok:
            raise self._request_error(response)
        if not response.content:
            return {"id": account_id, **payload}
        try:
            result = _account_from_response(response.json())
        except ValueError as exc:
            raise CRMError("The CRM update-account endpoint returned invalid JSON.") from exc
        if not isinstance(result, dict):
            raise CRMError("The CRM update-account endpoint returned an unrecognized response.")
        return result

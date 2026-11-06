from __future__ import annotations

import os
from typing import Any

import requests

CRM_API_ROOT = "https://analyst-assessment-production.up.railway.app/api/v1"
PAGE_SIZE = 50
MAX_PAGES = 100


class CRMError(RuntimeError):
    """A sanitized CRM API failure; never includes request authorization headers."""


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
                batch = self._records_from_response(response.json())
            except ValueError as exc:
                raise CRMError("The CRM accounts endpoint returned invalid JSON.") from exc
            if not batch:
                break
            for row in batch:
                account_id = row.get("id")
                if account_id is None or str(account_id) not in seen_ids:
                    accounts.append(row)
                    if account_id is not None:
                        seen_ids.add(str(account_id))
            if len(batch) < PAGE_SIZE:
                break
        else:
            raise CRMError(f"CRM pagination exceeded the safety limit of {MAX_PAGES} pages.")

        return accounts

    def create_account(self, payload: dict) -> dict:
        try:
            response = self.session.post(
                f"{CRM_API_ROOT}/accounts",
                json=payload,
                timeout=25,
            )
        except requests.RequestException as exc:
            raise CRMError(f"Could not create the approved CRM account: {exc}") from exc
        if not response.ok:
            raise self._request_error(response)
        try:
            result = response.json()
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
                json=payload,
                timeout=25,
            )
        except requests.RequestException as exc:
            raise CRMError(f"Could not update the approved CRM account: {exc}") from exc
        if not response.ok:
            raise self._request_error(response)
        if not response.content:
            return {"id": account_id, **payload}
        try:
            result = response.json()
        except ValueError as exc:
            raise CRMError("The CRM update-account endpoint returned invalid JSON.") from exc
        if not isinstance(result, dict):
            raise CRMError("The CRM update-account endpoint returned an unrecognized response.")
        return result

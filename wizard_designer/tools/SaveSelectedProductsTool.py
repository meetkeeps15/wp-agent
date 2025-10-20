import os
import json
from typing import List, Dict, Any, Optional

import requests
from agency_swarm.tools import BaseTool
from pydantic import Field
from dotenv import load_dotenv

from wizard_designer.utils.highlevel_client import upsert_contact_with_fields, API_BASE
try:
    from wizard_designer.utils.highlevel_client import ensure_contact  # type: ignore
except Exception:  # pragma: no cover
    ensure_contact = None  # type: ignore


class SaveSelectedProductsTool(BaseTool):
    """
    Save selected product SKUs to the current user's contact in HighLevel.

    IMPORTANT: The provided SKUs will OVERWRITE any previously saved SKUs.
    """

    skus: List[str] = Field(..., description="List of selected product SKUs to save (overwrites previous)")
    email: Optional[str] = Field(None, description="Optional: contact email to bind updates; otherwise session-scoped synthetic email is used")
    overwrite: bool = Field(True, description="Always overwrite previous SKUs (must be true)")

    def _ensure_product_skus_field(self, token: str, location_id: str) -> str:
        """Ensure a Product SKUs custom field exists; create as TEXT if missing.
        Returns the field ID.
        """
        existing = os.getenv("HL_CF_PRODUCT_SKUS", "").strip()
        if existing:
            return existing

        # Try multiple endpoint/payload variants to maximize compatibility across tenants
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Version": "2021-07-28",
            "LocationId": location_id,
        }
        url_variants = [
            f"{API_BASE}/custom-fields",
            f"{API_BASE}/customFields",
            f"{API_BASE}/locations/{location_id}/custom-fields",
            f"{API_BASE}/locations/{location_id}/customFields",
        ]
        payload_variants = [
            {"name": "Product SKUs", "dataType": "TEXT", "locationId": location_id},
            {"name": "Product SKUs", "dataType": "TEXT"},
            {"name": "Product SKUs", "dataType": "TEXTBOX_LIST", "locationId": location_id},
            {"name": "Product SKUs", "dataType": "TEXTBOX_LIST"},
        ]

        field_id: Optional[str] = None
        last_body: Dict[str, Any] = {}
        for url in url_variants:
            for payload in payload_variants:
                try:
                    resp = requests.post(url, headers=headers, json=payload, timeout=20)
                except Exception as e:
                    last_body = {"error": str(e)}
                    continue
                try:
                    body = resp.json()
                except Exception:
                    body = {"raw": resp.text}
                last_body = body
                if resp.status_code in (200, 201):
                    field_id = (body.get("customField") or {}).get("id") or body.get("id")
                    break
                if resp.status_code == 409:  # already exists
                    field_id = None
                    break
            if field_id is not None:
                break

        # If we did not get an id directly, try listing to discover
        if not field_id:
            list_variants = [
                f"{API_BASE}/custom-fields",
                f"{API_BASE}/customFields",
                f"{API_BASE}/locations/{location_id}/custom-fields",
                f"{API_BASE}/locations/{location_id}/customFields",
            ]
            for list_url in list_variants:
                try:
                    lr = requests.get(list_url, headers=headers, timeout=20)
                except Exception as e:
                    last_body = {"error": str(e)}
                    continue
                if not lr.ok:
                    try:
                        last_body = lr.json()
                    except Exception:
                        last_body = {"raw": lr.text}
                    continue
                try:
                    data = lr.json()
                except Exception:
                    data = {"items": []}
                items = data.get("customFields") or data.get("items") or data.get("list") or []
                for it in items:
                    if str(it.get("name", "")).strip().lower() == "product skus" and (it.get("locationId") in (None, location_id) or it.get("locationId") == location_id):
                        field_id = it.get("id")
                        break
                if field_id:
                    break

        if not field_id:
            raise RuntimeError(f"Failed to create/resolve Product SKUs field: {last_body}")

        # Set for current process so highlevel_client picks it up
        os.environ["HL_CF_PRODUCT_SKUS"] = field_id
        return field_id

    def run(self) -> Dict[str, Any]:  # type: ignore[override]
        load_dotenv()

        token = os.getenv("HIGHLEVEL_ACCESS_TOKEN") or os.getenv("HIGHLEVEL_TOKEN") or os.getenv("GHL_TOKEN")
        location_id = os.getenv("HIGHLEVEL_LOCATION_ID") or os.getenv("GHL_LOCATION_ID")
        if not token or not location_id:
            return {"status": "error", "message": "Missing HighLevel token or location id"}

        if not self.overwrite:
            return {"status": "error", "message": "overwrite must be true; tool overwrites SKUs by design"}

        # Ensure Product SKUs field exists (TEXT)
        try:
            field_id = self._ensure_product_skus_field(token, location_id)
        except Exception as e:
            return {"status": "error", "message": str(e)}

        # Save SKUs as comma-separated string
        sku_value = ",".join([s.strip() for s in self.skus if s and str(s).strip()])

        # Prefer updating the cached session contact if available; otherwise upsert by email/session
        try:
            from wizard_designer.utils.highlevel_client import _derive_session_key, _load_cached_contact, _resolve_field_ids  # type: ignore
        except Exception:
            _derive_session_key = None  # type: ignore
            _load_cached_contact = None  # type: ignore
            _resolve_field_ids = None  # type: ignore

        result: Dict[str, Any]
        cached_contact_id: Optional[str] = None
        if _derive_session_key and _load_cached_contact:
            try:
                skey = _derive_session_key()
                cached = _load_cached_contact(skey)
                cached_contact_id = (cached or {}).get("id")
            except Exception:
                cached_contact_id = None

        if cached_contact_id:
            try:
                headers = {
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Version": "2021-07-28",
                    "LocationId": location_id,
                }
                payload: Dict[str, Any] = {"tags": ["aaas", "selected-products"]}
                if _resolve_field_ids:
                    fids = _resolve_field_ids()
                    fid = fids.get("PRODUCT_SKUS")
                    if fid:
                        payload["customFields"] = [{"id": fid, "value": sku_value}]
                r = requests.put(f"{API_BASE}/contacts/{cached_contact_id}", headers=headers, json=payload, timeout=30)
                if not r.ok:
                    try:
                        body = r.json()
                    except Exception:
                        body = {"raw": r.text}
                    return {"status": "error", "message": f"Failed to update cached contact {cached_contact_id}: {r.status_code} {body}"}
                try:
                    result = r.json()
                except Exception:
                    result = {"raw": r.text}
            except Exception as e:
                return {"status": "error", "message": f"Direct cached update failed: {e}"}
        else:
            # No cached contact id; ensure single contact and attach SKUs
            if ensure_contact:
                result = ensure_contact(
                    email=self.email,
                    tags=["aaas", "selected-products"],
                    custom_fields_by_symbol={"PRODUCT_SKUS": sku_value},
                )
            else:
                result = upsert_contact_with_fields(
                    email=self.email,
                    tags=["aaas", "selected-products"],
                    custom_fields_by_symbol={"PRODUCT_SKUS": sku_value},
                )

        # Fetch contact data for return if possible
        try:
            contact_id = (result.get("contact") or {}).get("id") or result.get("id")
            if not contact_id:
                contact_id = result.get("contact_id")
            if contact_id:
                r = requests.get(
                    f"{API_BASE}/contacts/{contact_id}",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                        "Version": "2021-07-28",
                    },
                    timeout=20,
                )
                body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"raw": r.text}
            else:
                body = {"info": "contact id not returned"}
        except Exception as e:
            body = {"error": str(e)}

        return {"status": "success", "overwritten": True, "contact": body, "contact_id": contact_id}



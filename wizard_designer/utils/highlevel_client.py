import os
import json
from typing import Dict, Any, List, Optional

import requests
from dotenv import load_dotenv


API_BASE = "https://services.leadconnectorhq.com"


def _env(name: str, default: str = "") -> str:
    v = os.getenv(name)
    return v if v is not None and str(v).strip() != "" else default


def _headers(token: str, location_id: Optional[str] = None) -> Dict[str, str]:
    h = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Version": "2021-07-28",
    }
    if location_id:
        h["LocationId"] = location_id
    return h


# Load .env once on import so credentials are available when tools call helpers
try:
    load_dotenv(override=False)
except Exception:
    pass


def _resolve_field_ids() -> Dict[str, str]:
    """Resolve custom field IDs from env with sensible fallbacks (created earlier)."""
    return {
        "IG_HANDLE": _env("HL_CF_IG_HANDLE", "qeGZxU9HDjLh4fqox8P0"),
        "IG_FOLLOWERS": _env("HL_CF_IG_FOLLOWERS", "TnOg3Hx3oQYvZ8XFkdzF"),
        "TIKTOK_HANDLE": _env("HL_CF_TIKTOK_HANDLE", "jGSnwEscvSll6T777l8G"),
        "TIKTOK_FOLLOWERS": _env("HL_CF_TIKTOK_FOLLOWERS", "R8gkuL48aUJxnC2INpHy"),
        "BRAND_NAME": _env("HL_CF_BRAND_NAME", "THAfasnMIWf5rAPC4YJI"),
        "LOGO_URL": _env("HL_CF_LOGO_URL", "8oJXwurKogmEUNfsByPE"),
        "PRODUCT_MOCKUP_URL": _env("HL_CF_PRODUCT_MOCKUP_URL", "vrZmKfqO3ntKXYFpQKji"),
        # Product SKUs pending - optional
        "PRODUCT_SKUS": _env("HL_CF_PRODUCT_SKUS", ""),
    }


def _guess_mime_type(path: str) -> str:
    try:
        import mimetypes
        mt, _ = mimetypes.guess_type(path)
        return mt or "application/octet-stream"
    except Exception:
        return "application/octet-stream"


def upload_media(file_path: str, filename: Optional[str] = None, content_type: Optional[str] = None) -> Dict[str, Any]:
    """Upload a local file to HighLevel Media Storage and return a dict with best-effort URL.

    Tries multiple v2 endpoint variants and finally v1 if configured. Returns:
    {"ok": bool, "url": str|None, "raw": any, "error": str|None}
    """
    token = _env("HIGHLEVEL_ACCESS_TOKEN") or _env("HIGHLEVEL_TOKEN") or _env("GHL_TOKEN")
    location_id = _env("HIGHLEVEL_LOCATION_ID") or _env("GHL_LOCATION_ID")
    v1_token = _env("GHL_API_KEY") or _env("HIGHLEVEL_API_KEY") or _env("GHL_V1_API_KEY")
    if not token and not v1_token:
        return {"ok": False, "error": "Missing HIGHLEVEL_ACCESS_TOKEN or GHL_API_KEY"}

    name = filename or os.path.basename(file_path)
    ctype = content_type or _guess_mime_type(file_path)

    # Prepare file payload
    try:
        f = open(file_path, "rb")
    except Exception as e:
        return {"ok": False, "error": f"Open file failed: {e}"}

    def _headers_no_ct(tok: str, loc: Optional[str]) -> Dict[str, str]:
        h = {
            "Authorization": f"Bearer {tok}",
            "Accept": "application/json",
            "Version": "2021-07-28",
        }
        if loc:
            h["LocationId"] = loc
        return h

    files = {"file": (name, f, ctype)}
    v2_urls = [
        f"{API_BASE}/media/upload",
        f"{API_BASE}/media",
        f"{API_BASE}/locations/{location_id}/media",
        f"{API_BASE}/locations/{location_id}/media/upload",
    ] if location_id else [
        f"{API_BASE}/media/upload",
        f"{API_BASE}/media",
    ]

    try:
        # Try v2 variants first
        if token:
            for url in v2_urls:
                try:
                    r = requests.post(url, headers=_headers_no_ct(token, location_id), files=files, timeout=60)
                    if r.ok:
                        try:
                            data = r.json()
                        except Exception:
                            data = {"raw": r.text}
                        # Common fields seen across variants
                        url_val = (
                            (data.get("fileUrl") if isinstance(data, dict) else None)
                            or (data.get("url") if isinstance(data, dict) else None)
                            or (data.get("secureUrl") if isinstance(data, dict) else None)
                            or (data.get("media", {}).get("url") if isinstance(data, dict) else None)
                        )
                        return {"ok": True, "url": url_val, "raw": data}
                except Exception:
                    continue

        # Fallback to v1 if configured
        if v1_token:
            v1_base = "https://rest.gohighlevel.com/v1"
            try:
                r1 = requests.post(
                    f"{v1_base}/media",
                    headers={"Authorization": f"Bearer {v1_token}"},
                    files=files,
                    timeout=60,
                )
                if r1.ok:
                    try:
                        data1 = r1.json()
                    except Exception:
                        data1 = {"raw": r1.text}
                    url_val = (
                        (data1.get("fileUrl") if isinstance(data1, dict) else None)
                        or (data1.get("url") if isinstance(data1, dict) else None)
                        or (data1.get("secureUrl") if isinstance(data1, dict) else None)
                    )
                    return {"ok": True, "url": url_val, "raw": data1}
            except Exception:
                pass
    finally:
        try:
            f.close()
        except Exception:
            pass

    return {"ok": False, "error": "All media upload attempts failed"}


def _list_custom_fields(token: str, location_id: Optional[str]) -> List[Dict[str, Any]]:
    urls = [
        f"{API_BASE}/custom-fields",
        f"{API_BASE}/customFields",
    ]
    if location_id:
        urls.extend([
            f"{API_BASE}/locations/{location_id}/custom-fields",
            f"{API_BASE}/locations/{location_id}/customFields",
        ])
    for u in urls:
        try:
            r = requests.get(u, headers=_headers(token, location_id), timeout=30)
            if not r.ok:
                continue
            try:
                data = r.json()
            except Exception:
                continue
            items = data.get("customFields") or data.get("items") or data.get("list") or []
            if isinstance(items, list):
                return items
        except Exception:
            continue
    return []


def find_custom_field_id_by_name(name: str) -> Optional[str]:
    token = _env("HIGHLEVEL_ACCESS_TOKEN") or _env("HIGHLEVEL_TOKEN") or _env("GHL_TOKEN")
    location_id = _env("HIGHLEVEL_LOCATION_ID") or _env("GHL_LOCATION_ID")
    if not token:
        return None
    name_lower = name.strip().lower()
    items = _list_custom_fields(token, location_id)
    for it in items:
        it_name = str(it.get("name", "")).strip().lower()
        if it_name == name_lower:
            return it.get("id")
    return None


def create_text_custom_field(name: str) -> Optional[str]:
    token = _env("HIGHLEVEL_ACCESS_TOKEN") or _env("HIGHLEVEL_TOKEN") or _env("GHL_TOKEN")
    location_id = _env("HIGHLEVEL_LOCATION_ID") or _env("GHL_LOCATION_ID")
    if not token:
        return None
    payloads = [
        {"name": name, "dataType": "TEXT", "locationId": location_id},
        {"name": name, "dataType": "TEXT"},
    ]
    urls = [
        f"{API_BASE}/custom-fields",
        f"{API_BASE}/customFields",
    ]
    if location_id:
        urls.extend([
            f"{API_BASE}/locations/{location_id}/custom-fields",
            f"{API_BASE}/locations/{location_id}/customFields",
        ])
    for u in urls:
        for p in payloads:
            try:
                r = requests.post(u, headers=_headers(token, location_id), json=p, timeout=30)
                if r.ok:
                    try:
                        d = r.json()
                    except Exception:
                        d = {}
                    fid = d.get("id") or d.get("customFieldId") or (d.get("customField") or {}).get("id")
                    if fid:
                        return fid
            except Exception:
                continue
    return None


def get_or_create_custom_field_id(field_name: str) -> Optional[str]:
    fid = find_custom_field_id_by_name(field_name)
    if fid:
        return fid
    return create_text_custom_field(field_name)


def upsert_contact_with_fields(
    *,
    email: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    phone: Optional[str] = None,
    tags: Optional[List[str]] = None,
    custom_fields_by_symbol: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create or update a contact with mapped custom fields.

    custom_fields_by_symbol keys should be one of keys from _resolve_field_ids().
    """
    token = _env("HIGHLEVEL_ACCESS_TOKEN") or _env("HIGHLEVEL_TOKEN") or _env("GHL_TOKEN")
    location_id = _env("HIGHLEVEL_LOCATION_ID") or _env("GHL_LOCATION_ID")
    if not token or not location_id:
        return {"skipped": True, "reason": "Missing token/location"}

    # --- Session-aware identity to avoid cross-user mixups ---
    session_key = _derive_session_key()
    cached = _load_cached_contact(session_key)
    final_email = email or (cached.get("email") if cached else None) or f"{session_key}@example.com"

    field_ids = _resolve_field_ids()
    custom_fields_payload: List[Dict[str, Any]] = []
    for sym, value in (custom_fields_by_symbol or {}).items():
        fid = field_ids.get(sym)
        if fid and value is not None and value != "":
            custom_fields_payload.append({"id": fid, "value": value})

    payload: Dict[str, Any] = {
        "email": final_email,
        "locationId": location_id,
        "tags": tags or [],
        "source": "AAAS Tools",
    }
    if first_name:
        payload["firstName"] = first_name
    if last_name:
        payload["lastName"] = last_name
    if phone:
        payload["phone"] = phone
    if custom_fields_payload:
        payload["customFields"] = custom_fields_payload

    try:
        url = f"{API_BASE}/contacts/upsert"
        resp = requests.post(url, headers=_headers(token, location_id), json=payload, timeout=30)
        resp.raise_for_status()
        try:
            data = resp.json()
            # Cache contact id/email for this session
            contact_id = (data.get("contact") or {}).get("id") or data.get("id")
            if contact_id:
                _save_cached_contact(session_key, {"id": contact_id, "email": final_email})
            return data
        except Exception:
            return {"raw": resp.text}
    except Exception as e:
        return {"error": str(e)}


# ---------------- Session-scoped Contact Cache ----------------
def _derive_session_key() -> str:
    """Derive a stable short key from agency headers to isolate users.

    Prioritizes X-Chat-Id, then X-User-Id, then X-Agent-Id, falling back to CURSOR_* and a random UUID.
    """
    candidates = [
        "X-Chat-Id", "X-User-Id", "X-Agent-Id",
        "X-Chat-ID", "X-ChatId", "X-User-ID", "X-UserId",
        "CURSOR_SESSION_ID", "CURSOR_CHAT_ID", "CURSOR_TRACE_ID",
        "AGENCY_SESSION_ID", "AGENCY_CHAT_ID", "AGENCY_USER_ID",
    ]
    for h in candidates:
        v = _env(h.replace('-', '_').upper())
        if v:
            return str(v)[:8]
    import uuid
    return str(uuid.uuid4())[:8]


def _cache_dir() -> str:
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    path = os.path.join(root, "cache", "highlevel_contacts")
    os.makedirs(path, exist_ok=True)
    return path


def _cache_file(session_key: str) -> str:
    return os.path.join(_cache_dir(), f"{session_key}.json")


def _load_cached_contact(session_key: str) -> Dict[str, Any]:
    try:
        p = _cache_file(session_key)
        if os.path.exists(p):
            with open(p, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_cached_contact(session_key: str, payload: Dict[str, Any]) -> None:
    try:
        p = _cache_file(session_key)
        with open(p, 'w') as f:
            json.dump(payload, f)
    except Exception:
        pass



def ensure_contact(
    *,
    email: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    phone: Optional[str] = None,
    tags: Optional[List[str]] = None,
    custom_fields_by_symbol: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Ensure there is a single session-scoped contact and return its id and details.

    - Uses cached contact if available
    - Upserts with provided fields
    - Returns { ok, contact_id, email, raw }
    """
    token = _env("HIGHLEVEL_ACCESS_TOKEN") or _env("HIGHLEVEL_TOKEN") or _env("GHL_TOKEN")
    location_id = _env("HIGHLEVEL_LOCATION_ID") or _env("GHL_LOCATION_ID")
    if not token or not location_id:
        return {"ok": False, "error": "Missing token/location"}

    session_key = _derive_session_key()
    cached = _load_cached_contact(session_key)
    final_email = email or (cached.get("email") if cached else None) or f"{session_key}@example.com"

    result = upsert_contact_with_fields(
        email=final_email,
        first_name=first_name,
        last_name=last_name,
        phone=phone,
        tags=tags,
        custom_fields_by_symbol=custom_fields_by_symbol,
    )
    contact_id = (result.get("contact") or {}).get("id") or result.get("id")
    if contact_id:
        _save_cached_contact(session_key, {"id": contact_id, "email": final_email})
        return {"ok": True, "contact_id": contact_id, "email": final_email, "raw": result}
    return {"ok": False, "error": "No contact id in response", "raw": result}


import os
import json
from typing import Dict, Any, Optional, List

import requests
from agency_swarm.tools import BaseTool
from pydantic import Field
from dotenv import load_dotenv

from wizard_designer.utils.highlevel_client import upsert_contact_with_fields, API_BASE
# Optional cache helpers (used by other tools); import guardedly for robustness
try:
    from wizard_designer.utils.highlevel_client import _derive_session_key, _load_cached_contact  # type: ignore
except Exception:  # pragma: no cover
    _derive_session_key = None  # type: ignore
    _load_cached_contact = None  # type: ignore


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


class CalendarSchedulerTool(BaseTool):
    """
    HighLevel Calendar utility with two modes:
    - availability: fetch public time slots in a date/time range
    - book: create an appointment for a contact at a specific time

    Notes:
    - This uses API 2.0 (LeadConnector). Availability/booking may require OAuth/PIT scopes for calendars.
    - If your token lacks calendar scopes, the tool returns a helpful error.
    """

    mode: str = Field(..., description="availability | book")
    calendar_id: Optional[str] = Field(None, description="HighLevel Calendar ID. Defaults to env HIGHLEVEL_CALENDAR_ID")
    # Availability inputs
    start_iso: Optional[str] = Field(None, description="Start datetime (ISO 8601) for availability search")
    end_iso: Optional[str] = Field(None, description="End datetime (ISO 8601) for availability search")
    timezone: Optional[str] = Field("UTC", description="IANA timezone, e.g., America/New_York")
    # Booking inputs
    slot_start_iso: Optional[str] = Field(None, description="Appointment start datetime (ISO 8601)")
    slot_end_iso: Optional[str] = Field(None, description="Appointment end datetime (ISO 8601)")
    slot_index: Optional[int] = Field(None, description="Index into last fetched free slots (context-cached)")
    contact_id: Optional[str] = Field(None, description="Existing HighLevel contact ID to book against (overrides upsert)")
    contact_email: Optional[str] = Field(None, description="Email to bind appointment (contact will be created/upserted if needed)")
    contact_first_name: Optional[str] = Field(None, description="Contact first name (optional)")
    contact_last_name: Optional[str] = Field(None, description="Contact last name (optional)")
    meeting_location: Optional[str] = Field(None, description="Location string (e.g., Zoom link) if required by your calendar")

    def _ctx_get(self, key: str, default: Any = None) -> Any:
        try:
            ctx = getattr(self, "_context", None)
            if ctx is None:
                return default
            # Try common APIs
            if hasattr(ctx, "get"):
                return ctx.get(key, default)
            if hasattr(ctx, "get_value"):
                return ctx.get_value(key, default)
            if hasattr(ctx, "get_data"):
                return ctx.get_data(key, default)
            # Mapping-like fallback
            try:
                return ctx[key]
            except Exception:
                return default
        except Exception:
            return default

    def _ctx_set(self, key: str, value: Any) -> None:
        try:
            ctx = getattr(self, "_context", None)
            if ctx is None:
                return
            if hasattr(ctx, "set"):
                ctx.set(key, value)
                return
            if hasattr(ctx, "set_value"):
                ctx.set_value(key, value)
                return
            if hasattr(ctx, "set_data"):
                ctx.set_data(key, value)
                return
            # Mapping-like fallback
            try:
                ctx[key] = value
            except Exception:
                pass
        except Exception:
            pass

    def _get_creds(self) -> Dict[str, str]:
        token = os.getenv("HIGHLEVEL_ACCESS_TOKEN") or os.getenv("HIGHLEVEL_TOKEN") or os.getenv("GHL_TOKEN")
        location_id = os.getenv("HIGHLEVEL_LOCATION_ID") or os.getenv("GHL_LOCATION_ID")
        if not token or not location_id:
            raise RuntimeError("Missing HIGHLEVEL_ACCESS_TOKEN or HIGHLEVEL_LOCATION_ID")
        return {"token": token, "location_id": location_id}

    def _get_calendar_id(self) -> str:
        # Prefer explicit param, then env, then known test ID as final fallback
        env_id = os.getenv("HIGHLEVEL_CALENDAR_ID") or os.getenv("GHL_CALENDAR_ID")
        return self.calendar_id or env_id or "UL9SNgWU3gjlVPKyzTMv"

    def _try_get(self, paths: List[str], headers: Dict[str, str], params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        last = {"error": "No attempts made"}
        for p in paths:
            url = f"{API_BASE}{p}"
            try:
                r = requests.get(url, headers=headers, params=params, timeout=30)
                try:
                    body = r.json()
                except Exception:
                    body = {"raw": r.text}
                if 200 <= r.status_code < 300:
                    return {"status": r.status_code, "body": body}
                last = {"status": r.status_code, "body": body}
            except Exception as e:
                last = {"error": str(e), "path": p}
        return last

    def _try_post(self, paths: List[str], headers: Dict[str, str], payload: Dict[str, Any]) -> Dict[str, Any]:
        last = {"error": "No attempts made"}
        for p in paths:
            url = f"{API_BASE}{p}"
            try:
                r = requests.post(url, headers=headers, json=payload, timeout=30)
                try:
                    body = r.json()
                except Exception:
                    body = {"raw": r.text}
                if 200 <= r.status_code < 300:
                    return {"status": r.status_code, "body": body}
                last = {"status": r.status_code, "body": body}
            except Exception as e:
                last = {"error": str(e), "path": p}
        return last

    def _list_calendars(self, token: str, location_id: str) -> Dict[str, Any]:
        headers = _headers(token, location_id)
        res = self._try_get(["/calendars/"], headers)
        return res

    def _availability(self, token: str, location_id: str) -> Dict[str, Any]:
        if not self.start_iso or not self.end_iso:
            return {"status": "error", "message": "start_iso and end_iso are required"}

        cal_id = self._get_calendar_id()
        headers = _headers(token, location_id)

        # Try to list calendars but proceed even if forbidden and we already have cal_id
        items: List[Dict[str, Any]] = []
        cal_list = self._list_calendars(token, location_id)
        if cal_list.get("status") in (200, 201):
            calendars = cal_list.get("body") or {}
            items = calendars.get("calendars") or calendars.get("items") or calendars.get("data") or []
        if not cal_id and items:
            cal_id = items[0].get("id")
        if not cal_id:
            return {"status": "error", "message": "No calendar_id available. Set HIGHLEVEL_CALENDAR_ID or pass calendar_id."}

        # Prefer epoch ms per tenant behavior (no timeZone param)
        def _iso_to_ms(s: str) -> int:
            from datetime import datetime, timezone
            v = s.strip()
            if v.endswith("Z"):
                v = v.replace("Z", "+00:00")
            dt = datetime.fromisoformat(v)
            if not dt.tzinfo:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)

        attempts = []
        try:
            start_ms = _iso_to_ms(self.start_iso)
            end_ms = _iso_to_ms(self.end_iso)
            params_ms = {"startDate": start_ms, "endDate": end_ms}
            res_ms = self._try_get([f"/calendars/{cal_id}/free-slots"], headers, params=params_ms)
            attempts.append({"variant": "startDate/endDate", "res": res_ms})
            if res_ms.get("status") in (200, 201):
                return {"status": "success", "availability": res_ms.get("body"), "calendar_id": cal_id}

            # With timeZone
            if self.timezone:
                params_ms_tz = {"startDate": start_ms, "endDate": end_ms, "timeZone": self.timezone}
                res_ms_tz = self._try_get([f"/calendars/{cal_id}/free-slots"], headers, params=params_ms_tz)
                attempts.append({"variant": "startDate/endDate + timeZone", "res": res_ms_tz})
                if res_ms_tz.get("status") in (200, 201):
                    return {"status": "success", "availability": res_ms_tz.get("body"), "calendar_id": cal_id}
        except Exception:
            pass

        # ISO fallback without timeZone
        params_iso = {"start": self.start_iso, "end": self.end_iso}
        res_iso = self._try_get([f"/calendars/{cal_id}/free-slots"], headers, params=params_iso)
        attempts.append({"variant": "start/end ISO", "res": res_iso})
        if res_iso.get("status") in (200, 201):
            return {"status": "success", "availability": res_iso.get("body"), "calendar_id": cal_id}

        # ISO with timeZone
        if self.timezone:
            params_iso_tz = {"start": self.start_iso, "end": self.end_iso, "timeZone": self.timezone}
            res_iso_tz = self._try_get([f"/calendars/{cal_id}/free-slots"], headers, params=params_iso_tz)
            attempts.append({"variant": "start/end ISO + timeZone", "res": res_iso_tz})
            if res_iso_tz.get("status") in (200, 201):
                return {"status": "success", "availability": res_iso_tz.get("body"), "calendar_id": cal_id}

        # Alternate param names
        params_alt = {"startTime": self.start_iso, "endTime": self.end_iso}
        res_alt = self._try_get([f"/calendars/{cal_id}/free-slots"], headers, params=params_alt)
        attempts.append({"variant": "startTime/endTime ISO", "res": res_alt})
        if res_alt.get("status") in (200, 201):
            return {"status": "success", "availability": res_alt.get("body"), "calendar_id": cal_id}

        # Alternate with timeZone
        if self.timezone:
            params_alt_tz = {"startTime": self.start_iso, "endTime": self.end_iso, "timeZone": self.timezone}
            res_alt_tz = self._try_get([f"/calendars/{cal_id}/free-slots"], headers, params=params_alt_tz)
            attempts.append({"variant": "startTime/endTime ISO + timeZone", "res": res_alt_tz})
            if res_alt_tz.get("status") in (200, 201):
                return {"status": "success", "availability": res_alt_tz.get("body"), "calendar_id": cal_id}

        return {"status": "error", "message": "Free slots API not accessible or no slots in range", "detail": attempts}

    def _ms_to_iso(self, value: Any) -> Optional[str]:
        try:
            if isinstance(value, (int, float)):
                from datetime import datetime, timezone
                dt = datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc)
                return dt.isoformat().replace("+00:00", "Z")
        except Exception:
            pass
        return None

    def _extract_first_slot(self, availability: Any) -> Optional[List[str]]:
        """Attempt to find the first slot in various common API shapes.

        Returns [start_iso, end_iso] if found, else None.
        """
        def iter_slots(obj: Any):
            if isinstance(obj, dict):
                # If looks like a slot object itself
                if any(k in obj for k in ("startTime", "endTime", "start", "end")):
                    yield obj
                # Explore common container keys
                for k in ("slots", "data", "items", "availability", "body"):
                    if k in obj:
                        yield from iter_slots(obj[k])
                # Also iterate all values to handle date-keyed dicts like {"2025-09-29": {"slots": [...]}}
                for v in obj.values():
                    yield from iter_slots(v)
            elif isinstance(obj, list):
                for it in obj:
                    yield from iter_slots(it)
            elif isinstance(obj, str):
                # Only treat strings that look like ISO datetimes
                if self._is_iso_datetime(obj):
                    yield {"start": obj, "end": self._add_minutes(obj, 30)}

        for slot in iter_slots(availability):
            start = slot.get("startTime") if isinstance(slot, dict) else None
            end = slot.get("endTime") if isinstance(slot, dict) else None
            if start is None and isinstance(slot, dict):
                start = slot.get("start")
            if end is None and isinstance(slot, dict):
                end = slot.get("end")

            # Convert ms epoch if needed
            if isinstance(start, (int, float)):
                start = self._ms_to_iso(start)
            if isinstance(end, (int, float)):
                end = self._ms_to_iso(end)

            if isinstance(start, str) and isinstance(end, str):
                return [start, end]
        return None

    def _normalize_slots(self, availability: Any) -> List[Dict[str, str]]:
        """Extract a flat, ordered list of slot dicts: {start_iso, end_iso}."""
        slots: List[Dict[str, str]] = []
        def iter_slots(obj: Any):
            if isinstance(obj, dict):
                if any(k in obj for k in ("startTime", "endTime", "start", "end")):
                    yield obj
                for k in ("slots", "data", "items", "availability", "body"):
                    if k in obj:
                        yield from iter_slots(obj[k])
                # Also iterate all values to handle date-keyed dicts like {"2025-09-29": {"slots": [...]}}
                for v in obj.values():
                    yield from iter_slots(v)
            elif isinstance(obj, list):
                for it in obj:
                    yield from iter_slots(it)
            elif isinstance(obj, str):
                # Only include plausible ISO datetime strings
                if self._is_iso_datetime(obj):
                    yield {"start": obj, "end": self._add_minutes(obj, 30)}
        for slot in iter_slots(availability):
            s = slot.get("startTime") if isinstance(slot, dict) else None
            e = slot.get("endTime") if isinstance(slot, dict) else None
            if s is None and isinstance(slot, dict):
                s = slot.get("start")
            if e is None and isinstance(slot, dict):
                e = slot.get("end")
            if isinstance(s, (int, float)):
                s = self._ms_to_iso(s)
            if isinstance(e, (int, float)):
                e = self._ms_to_iso(e)
            if isinstance(s, str) and isinstance(e, str):
                slots.append({"start_iso": s, "end_iso": e})
        # Deduplicate and sort
        seen = set()
        unique: List[Dict[str, str]] = []
        for sl in slots:
            key = (sl["start_iso"], sl["end_iso"])
            if key not in seen:
                seen.add(key)
                unique.append(sl)
        unique.sort(key=lambda x: x["start_iso"])  # lexicographic ISO sorts by time when Z/offset is present
        return unique

    # (Removed compact availability helper to keep tool output focused on slots)

    def _add_minutes(self, iso_str: str, minutes: int) -> str:
        from datetime import datetime, timedelta
        v = iso_str.strip()
        # Support Z or offset formats
        try:
            if v.endswith("Z"):
                v = v.replace("Z", "+00:00")
            dt = datetime.fromisoformat(v)
            dt2 = dt + timedelta(minutes=minutes)
            s = dt2.isoformat()
            # Preserve Z if original had Z
            if iso_str.strip().endswith("Z"):
                s = s.replace("+00:00", "Z")
            return s
        except Exception:
            # Fallback: just return original if parsing fails
            return iso_str

    def _is_iso_datetime(self, value: str) -> bool:
        try:
            s = value.strip()
            if s.endswith("Z"):
                s = s.replace("Z", "+00:00")
            from datetime import datetime
            datetime.fromisoformat(s)
            return True
        except Exception:
            return False

    def _book(self, token: str, location_id: str) -> Dict[str, Any]:
        cal_id = self._get_calendar_id()
        if not cal_id:
            return {"status": "error", "message": "Missing calendar_id (and HIGHLEVEL_CALENDAR_ID not set)"}

        # If slot_index provided, try to use cached slots first
        if self.slot_index is not None and (not self.slot_start_iso or not self.slot_end_iso):
            cache_key = f"calendar_slots:{cal_id}"
            cached = self._ctx_get(cache_key, [])
            if isinstance(cached, list) and 0 <= int(self.slot_index) < len(cached):
                chosen = cached[int(self.slot_index)]
                self.slot_start_iso = chosen.get("start_iso")
                self.slot_end_iso = chosen.get("end_iso")

        # If slot times are still missing, fetch availability and pick the first free slot
        if not self.slot_start_iso or not self.slot_end_iso:
            from datetime import datetime, timedelta, timezone

            # Save current range to restore later (best-effort)
            prev_start, prev_end = self.start_iso, self.end_iso
            try:
                now = datetime.now(timezone.utc)
                self.start_iso = (now).isoformat().replace("+00:00", "Z")
                self.end_iso = (now + timedelta(days=7)).isoformat().replace("+00:00", "Z")
                avail = self._availability(token, location_id)
                if avail.get("status") == "success":
                    normalized = avail.get("normalized_slots")
                    if isinstance(normalized, list) and normalized:
                        if self.slot_index is not None and 0 <= int(self.slot_index) < len(normalized):
                            chosen = normalized[int(self.slot_index)]
                            self.slot_start_iso, self.slot_end_iso = chosen["start_iso"], chosen["end_iso"]
                        else:
                            self.slot_start_iso, self.slot_end_iso = normalized[0]["start_iso"], normalized[0]["end_iso"]
                    else:
                        # Fallback to first slot extractor
                        slot = self._extract_first_slot(avail.get("availability"))
                        if slot:
                            self.slot_start_iso, self.slot_end_iso = slot[0], slot[1]
                # If still missing, error out
                if not self.slot_start_iso or not self.slot_end_iso:
                    return {"status": "error", "message": "No free slots found in the next 7 days"}
            finally:
                self.start_iso, self.end_iso = prev_start, prev_end

        # Resolve contact id: explicit -> cached -> upsert
        contact_id = (self.contact_id or "").strip()
        cached_detail: Dict[str, Any] = {}
        if not contact_id and _derive_session_key and _load_cached_contact:
            try:
                skey = _derive_session_key()
                cached_detail = _load_cached_contact(skey) or {}
                cid = cached_detail.get("id")
                if isinstance(cid, str) and cid.strip():
                    contact_id = cid.strip()
            except Exception:
                pass
        upsert: Dict[str, Any] = {}
        if not contact_id:
            # Ensure contact exists (or create) using email if provided; otherwise session-scoped synthetic
            upsert = upsert_contact_with_fields(
                email=self.contact_email,
                first_name=self.contact_first_name,
                last_name=self.contact_last_name,
                tags=["aaas", "calendar-booking"],
            )
            contact_id = (upsert.get("contact") or {}).get("id") or upsert.get("id")
        if not contact_id:
            return {"status": "error", "message": "Could not ensure contact", "detail": upsert or cached_detail}

        headers = _headers(token, location_id)
        payload = {
            "calendarId": cal_id,
            "contactId": contact_id,
            "startTime": self.slot_start_iso,
            "endTime": self.slot_end_iso,
            "timeZone": self.timezone or "UTC",
            "location": self.meeting_location,
            "locationId": location_id,
        }
        # Try documented endpoint first, then fallbacks
        paths = [
            "/calendars/events/appointments",  # HighLevel API 2.0 documented endpoint
            "/appointments",
            f"/calendars/{cal_id}/appointments",
            f"/locations/{location_id}/appointments",
        ]
        res = self._try_post(paths, headers, payload)
        if res.get("status") in (200, 201):
            return {"status": "success", "appointment": res.get("body")}
        return {"status": "error", "message": "Calendar booking API not accessible with current token/scopes", "detail": res}

    def run(self) -> Dict[str, Any]:  # type: ignore[override]
        load_dotenv(override=False)
        try:
            creds = self._get_creds()
        except Exception as e:
            return {"status": "error", "message": str(e)}

        token = creds["token"]
        location_id = creds["location_id"]

        if self.mode == "availability":
            raw = self._availability(token, location_id)
            if raw.get("status") == "success":
                cal_id = raw.get("calendar_id") or self._get_calendar_id()
                normalized = self._normalize_slots(raw.get("availability"))
                cache_key = f"calendar_slots:{cal_id}"
                # Cache normalized slots for follow-up booking by index
                self._ctx_set(cache_key, normalized[:200])
                return {
                    "status": "success",
                    "calendar_id": cal_id,
                    "normalized_slots": normalized,
                    "context_key": cache_key,
                }
            return raw
        if self.mode == "book":
            return self._book(token, location_id)
        return {"status": "error", "message": "mode must be one of: availability | book"}



if __name__ == "__main__":
    # Basic test harness
    load_dotenv(override=False)
    test_calendar = os.getenv("HIGHLEVEL_CALENDAR_ID") or os.getenv("GHL_CALENDAR_ID") or "UL9SNgWU3gjlVPKyzTMv"

    # 1) Availability test for the next 3 days (safe, read-only)
    try:
        from datetime import datetime, timedelta, timezone
        start = datetime.now(timezone.utc)
        end = start + timedelta(days=3)
        tool = CalendarSchedulerTool(
            mode="availability",
            calendar_id=test_calendar,
            start_iso=start.isoformat().replace("+00:00", "Z"),
            end_iso=end.isoformat().replace("+00:00", "Z"),
            timezone=os.getenv("TZ") or "UTC",
        )
        avail_res = tool.run()
        print(json.dumps(avail_res, indent=2))
    except Exception as e:
        print(json.dumps({"status": "error", "message": str(e)}))

    # 2) Optional booking test (requires env RUN_BOOKING_TEST=1); auto-picks first slot
    if (os.getenv("RUN_BOOKING_TEST") or "0") == "1":
        try:
            slot_idx_env = os.getenv("BOOK_SLOT_INDEX")
            idx = int(slot_idx_env) if slot_idx_env and slot_idx_env.isdigit() else None
            book_tool = CalendarSchedulerTool(
                mode="book",
                calendar_id=test_calendar,
                contact_email=os.getenv("TEST_CONTACT_EMAIL") or "test@example.com",
                contact_first_name=os.getenv("TEST_CONTACT_FIRST_NAME") or "Test",
                contact_last_name=os.getenv("TEST_CONTACT_LAST_NAME") or "User",
                timezone=os.getenv("TZ") or "UTC",
                slot_index=idx,
                contact_id=os.getenv("TEST_CONTACT_ID"),
            )
            print(json.dumps(book_tool.run(), indent=2))
        except Exception as e:
            print(json.dumps({"status": "error", "message": str(e)}))


import os
import json
from typing import Dict, Any, Optional

from agency_swarm.tools import BaseTool
from pydantic import Field
from dotenv import load_dotenv


class CheckTimeTool(BaseTool):
    """
    Returns current time information in UTC and a target timezone (from input or TZ env).

    Output includes:
    - utc: ISO string, date, time, weekday, timestamp_ms
    - local: timezone name, ISO string, date, time, weekday, offset, abbreviation
    - anchors: start/end of "today" in local tz (ISO), next_7_days range
    """

    timezone: Optional[str] = Field(
        None,
        description="IANA timezone like 'America/New_York'. Defaults to TZ env or UTC.",
    )

    def run(self) -> Dict[str, Any]:  # type: ignore[override]
        load_dotenv(override=False)
        try:
            from datetime import datetime, timedelta, timezone
            try:
                # Python 3.9+
                from zoneinfo import ZoneInfo  # type: ignore
            except Exception:  # pragma: no cover
                ZoneInfo = None  # type: ignore

            # Resolve tz
            tz_name = (self.timezone or os.getenv("TZ") or "UTC").strip()
            tz = None
            if tz_name and tz_name.upper() != "UTC" and ZoneInfo is not None:
                try:
                    tz = ZoneInfo(tz_name)
                except Exception:
                    tz = None

            # Now values
            now_utc = datetime.now(timezone.utc)
            if tz is not None:
                now_local = now_utc.astimezone(tz)
            else:
                # Fallback: treat as UTC
                tz_name = "UTC"
                now_local = now_utc

            # Helpers
            def fmt(dt: datetime) -> Dict[str, Any]:
                return {
                    "iso": dt.isoformat().replace("+00:00", "Z") if dt.utcoffset() == timedelta(0) else dt.isoformat(),
                    "date": dt.strftime("%Y-%m-%d"),
                    "time": dt.strftime("%H:%M:%S"),
                    "weekday": dt.strftime("%A"),
                }

            # Offsets
            offset_td = now_local.utcoffset() or timedelta(0)
            offset_total_min = int(offset_td.total_seconds() // 60)
            offset_sign = "+" if offset_total_min >= 0 else "-"
            offset_h = abs(offset_total_min) // 60
            offset_m = abs(offset_total_min) % 60
            offset_str = f"{offset_sign}{offset_h:02d}:{offset_m:02d}"
            tz_abbr = now_local.tzname() or tz_name

            # Anchors for availability windows
            start_of_today_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
            end_of_today_local = now_local.replace(hour=23, minute=59, second=59, microsecond=0)
            next_7_end_local = start_of_today_local + timedelta(days=7, seconds=-1)

            result = {
                "utc": {
                    **fmt(now_utc),
                    "timestamp_ms": int(now_utc.timestamp() * 1000),
                },
                "local": {
                    **fmt(now_local),
                    "timezone": tz_name,
                    "offset": offset_str,
                    "abbreviation": tz_abbr,
                },
                "anchors": {
                    "today_local": {
                        "start_iso": fmt(start_of_today_local)["iso"],
                        "end_iso": fmt(end_of_today_local)["iso"],
                    },
                    "next_7_days_local": {
                        "start_iso": fmt(start_of_today_local)["iso"],
                        "end_iso": fmt(next_7_end_local)["iso"],
                    },
                },
            }
            return result
        except Exception as e:
            return {"status": "error", "message": str(e)}


if __name__ == "__main__":
    tool = CheckTimeTool()
    print(json.dumps(tool.run(), indent=2))



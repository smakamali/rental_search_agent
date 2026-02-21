"""Google Calendar API wrapper for listing availability and event management."""

import logging
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from typing import Any

_CREDENTIALS_LOCK = threading.RLock()  # RLock: reentrant; get_credentials is called from get_or_create_realtor_calendar_id while lock is held

SCOPES = ["https://www.googleapis.com/auth/calendar"]
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CREDENTIALS_PATH = _PROJECT_ROOT / ".rental_search_agent" / "credentials.json"
DEFAULT_TOKEN_PATH = _PROJECT_ROOT / ".rental_search_agent" / "token.json"

REALTOR_CALENDAR_NAME = "Realtor Agent"

logger = logging.getLogger(__name__)

# Weekday numbers: 0=Mon, 6=Sun
WEEKDAYS = {0, 1, 2, 3, 4}
WEEKENDS = {5, 6}


def parse_preferred_times(preferred_times: str) -> tuple[set[int], int, int]:
    """Parse preferred viewing times into day mask and hour range.

    Returns (day_mask, start_hour, end_hour).
    day_mask: set of weekday ints 0-6 (0=Mon, 6=Sun).
    start_hour, end_hour: 0-23.

    Default if unparseable: all days, 9-17.
    """
    s = (preferred_times or "").strip().lower()
    if not s:
        return (set(range(7)), 9, 17)

    days: set[int] = set(range(7))
    start_hour, end_hour = 9, 17

    # Day patterns
    if "weekday" in s or "week days" in s:
        days = WEEKDAYS
    elif "weekend" in s or "week end" in s:
        days = WEEKENDS
    elif "mon" in s or "monday" in s or "tue" in s or "tuesday" in s:
        if "weekend" not in s:
            days = WEEKDAYS
    elif "sat" in s or "sunday" in s:
        days = WEEKENDS

    # Time patterns: 6-8pm, 6–8pm, 6-8 pm, 10am-2pm, 9–5, 18:00-20:00
    time_match = re.search(
        r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*[-–to]+\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?",
        s,
        re.IGNORECASE,
    )
    if time_match:
        h1, m1, ap1 = int(time_match.group(1)), int(time_match.group(2) or 0), (time_match.group(3) or "").lower()
        h2, m2, ap2 = int(time_match.group(4)), int(time_match.group(5) or 0), (time_match.group(6) or "").lower()
        if ap1 == "pm" and h1 < 12:
            h1 += 12
        elif ap1 == "am" and h1 == 12:
            h1 = 0
        if ap2 == "pm" and h2 < 12:
            h2 += 12
        elif ap2 == "am" and h2 == 12:
            h2 = 0
        if ap2 and not ap1 and h1 <= 12 and h1 < h2:
            h1 += 12
        start_hour = h1 + m1 / 60
        end_hour = h2 + m2 / 60
        # Keep as int hours for simplicity
        start_hour = int(start_hour)
        end_hour = int(end_hour)
        if end_hour <= start_hour:
            end_hour = start_hour + 2

    # Simpler patterns: "evenings", "6pm", "6-8"
    if "evening" in s and not time_match:
        start_hour, end_hour = 18, 20
    if "morning" in s and not time_match:
        start_hour, end_hour = 9, 12
    if "afternoon" in s and not time_match:
        start_hour, end_hour = 13, 17

    return (days, start_hour, end_hour)


def get_credentials(scopes: list[str] | None = None) -> Any:
    """Load/refresh OAuth credentials. Uses env vars for paths if set. Thread-safe."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    logger.debug("get_credentials: acquiring lock")
    creds_path = os.environ.get("GOOGLE_CALENDAR_CREDENTIALS_PATH") or str(DEFAULT_CREDENTIALS_PATH)
    token_path = os.environ.get("GOOGLE_CALENDAR_TOKEN_PATH") or str(DEFAULT_TOKEN_PATH)
    scopes = scopes or SCOPES

    with _CREDENTIALS_LOCK:
        logger.debug("get_credentials: loading token from %s", token_path)
        creds = None
        if os.path.exists(token_path):
            try:
                creds = Credentials.from_authorized_user_file(token_path, scopes)
                logger.debug("get_credentials: loaded token, valid=%s", creds.valid)
            except Exception as e:
                logger.debug("get_credentials: failed to load token: %s", e)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                logger.debug("get_credentials: refreshing token")
                creds.refresh(Request())
            else:
                if not os.path.exists(creds_path):
                    logger.debug("get_credentials: credentials not found at %s", creds_path)
                    raise ValueError(
                        f"Google Calendar credentials not found at {creds_path}. "
                        "Download credentials.json from Google Cloud Console and place it there."
                    )
                logger.debug("get_credentials: starting OAuth flow (browser will open)")
                flow = InstalledAppFlow.from_client_secrets_file(creds_path, scopes)
                creds = flow.run_local_server(port=0)
                logger.debug("get_credentials: OAuth flow completed")
            Path(token_path).parent.mkdir(parents=True, exist_ok=True)
            with open(token_path, "w") as f:
                f.write(creds.to_json())
        return creds


def _get_service():
    """Build Calendar API service."""
    from googleapiclient.discovery import build

    creds = get_credentials()
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def get_or_create_realtor_calendar_id() -> str:
    """Return the Realtor Agent calendar ID. Creates the calendar if it does not exist."""
    logger.debug("get_or_create_realtor_calendar_id: acquiring lock")
    with _CREDENTIALS_LOCK:
        logger.debug("get_or_create_realtor_calendar_id: building service")
        service = _get_service()
        page_token = None
        while True:
            logger.debug("get_or_create_realtor_calendar_id: listing calendars (page_token=%s)", bool(page_token))
            result = service.calendarList().list(pageToken=page_token).execute()
            for item in result.get("items", []):
                if item.get("summary") == REALTOR_CALENDAR_NAME:
                    cal_id = item["id"]
                    logger.debug("get_or_create_realtor_calendar_id: found existing calendar %s", cal_id[:20] + "...")
                    return cal_id
            page_token = result.get("nextPageToken")
            if not page_token:
                break
        logger.debug("get_or_create_realtor_calendar_id: creating new calendar")
        created = (
            service.calendars()
            .insert(body={"summary": REALTOR_CALENDAR_NAME, "timeZone": "America/Vancouver"})
            .execute()
        )
        logger.debug("get_or_create_realtor_calendar_id: created calendar %s", created["id"][:20] + "...")
        return created["id"]


def list_events(
    time_min: str,
    time_max: str,
    calendar_id: str | None = None,
    max_results: int = 50,
) -> list[dict[str, Any]]:
    """List events in the given time range. Defaults to Realtor Agent calendar."""
    if calendar_id is None or calendar_id == "primary":
        calendar_id = get_or_create_realtor_calendar_id()
    service = _get_service()
    result = (
        service.events()
        .list(
            calendarId=calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    return result.get("items", [])


def _to_rfc3339(dt_str: str, tz: ZoneInfo) -> str:
    """Ensure datetime string is RFC3339 with timezone for Calendar API."""
    s = (dt_str or "").strip()
    if not s:
        raise ValueError("Empty datetime string")
    # Parse; if no timezone, treat as local in tz
    s_norm = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s_norm)
    except ValueError:
        # Try date-only, e.g. 2026-02-25
        if "T" not in s:
            s = s + "T00:00:00"
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    else:
        dt = dt.astimezone(tz)
    return dt.isoformat()  # RFC3339


def get_available_slots(
    preferred_times: str,
    time_min: str,
    time_max: str,
    slot_duration_minutes: int = 60,
    timezone: str = "America/Vancouver",
) -> list[dict[str, Any]]:
    """Get available slots within preferred times. Uses FreeBusy on primary + Realtor Agent calendars."""
    from googleapiclient.discovery import build

    logger.debug("get_available_slots: start (time_min=%s time_max=%s)", time_min, time_max)
    tz = ZoneInfo(timezone)
    time_min_rfc = _to_rfc3339(time_min, tz)
    time_max_rfc = _to_rfc3339(time_max, tz)
    logger.debug("get_available_slots: getting/creating Realtor calendar")
    realtor_id = get_or_create_realtor_calendar_id()
    logger.debug("get_available_slots: Realtor calendar id resolved")

    logger.debug("get_available_slots: getting credentials")
    creds = get_credentials()
    logger.debug("get_available_slots: building service")
    service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    body = {
        "timeMin": time_min_rfc,
        "timeMax": time_max_rfc,
        "items": [{"id": "primary"}, {"id": realtor_id}],
    }
    logger.debug("get_available_slots: calling FreeBusy API")
    freebusy = service.freebusy().query(body=body).execute()
    logger.debug("get_available_slots: FreeBusy API returned")
    calendars_data = freebusy.get("calendars", {})
    busy_list: list[dict[str, Any]] = []
    for cal_id in ("primary", realtor_id):
        cal = calendars_data.get(cal_id, {})
        if "errors" in cal:
            raise ValueError("Calendar access error: " + str(cal["errors"]))
        busy_list.extend(cal.get("busy", []))

    logger.debug("get_available_slots: computing slots (%d busy periods)", len(busy_list))
    days_mask, start_hour, end_hour = parse_preferred_times(preferred_times)
    slots: list[dict[str, Any]] = []

    start_dt = datetime.fromisoformat(time_min_rfc.replace("Z", "+00:00"))
    end_dt = datetime.fromisoformat(time_max_rfc.replace("Z", "+00:00"))
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=tz)
    else:
        start_dt = start_dt.astimezone(tz)
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=tz)
    else:
        end_dt = end_dt.astimezone(tz)

    def _busy_to_local(b: dict) -> tuple[datetime, datetime]:
        s = datetime.fromisoformat(b["start"].replace("Z", "+00:00"))
        e = datetime.fromisoformat(b["end"].replace("Z", "+00:00"))
        if s.tzinfo is None:
            s = s.replace(tzinfo=timezone.utc)
        if e.tzinfo is None:
            e = e.replace(tzinfo=timezone.utc)
        return s.astimezone(tz), e.astimezone(tz)

    busy_local = [_busy_to_local(b) for b in busy_list]
    current = start_dt
    slot_delta = timedelta(minutes=slot_duration_minutes)

    while current + slot_delta <= end_dt:
        slot_end = current + slot_delta
        py_weekday = current.weekday()
        if py_weekday not in days_mask:
            current = current.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            continue
        hour = current.hour + current.minute / 60
        if hour < start_hour or hour + slot_duration_minutes / 60 > end_hour:
            current += slot_delta
            continue

        overlaps = any(
            current < be and slot_end > bs for bs, be in busy_local
        )
        if not overlaps:
            slots.append({
                "start": current.strftime("%Y-%m-%dT%H:%M:%S"),
                "end": slot_end.strftime("%Y-%m-%dT%H:%M:%S"),
                "display": current.strftime("%A %b %d, %I:%M%p"),
            })
        current += slot_delta
    logger.debug("get_available_slots: done, found %d slots", len(slots))
    return slots


def create_event(
    summary: str,
    start_datetime: str,
    end_datetime: str,
    description: str | None = None,
    location: str | None = None,
    timezone: str = "America/Vancouver",
    extended_properties: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Create a calendar event in the Realtor Agent calendar."""
    calendar_id = get_or_create_realtor_calendar_id()
    service = _get_service()
    body = {
        "summary": summary,
        "start": {"dateTime": start_datetime, "timeZone": timezone},
        "end": {"dateTime": end_datetime, "timeZone": timezone},
    }
    if description:
        body["description"] = description
    if location:
        body["location"] = location
    if extended_properties:
        body["extendedProperties"] = {"private": extended_properties}
    return service.events().insert(calendarId=calendar_id, body=body).execute()


def update_event(
    event_id: str,
    summary: str | None = None,
    start_datetime: str | None = None,
    end_datetime: str | None = None,
    description: str | None = None,
    location: str | None = None,
    timezone: str = "America/Vancouver",
) -> dict[str, Any]:
    """Update an existing event in the Realtor Agent calendar."""
    calendar_id = get_or_create_realtor_calendar_id()
    service = _get_service()
    event = service.events().get(calendarId=calendar_id, eventId=event_id).execute()
    if summary is not None:
        event["summary"] = summary
    if start_datetime is not None:
        event["start"] = {"dateTime": start_datetime, "timeZone": timezone}
    if end_datetime is not None:
        event["end"] = {"dateTime": end_datetime, "timeZone": timezone}
    if description is not None:
        event["description"] = description
    if location is not None:
        event["location"] = location
    return service.events().update(calendarId=calendar_id, eventId=event_id, body=event).execute()


def delete_event(event_id: str) -> None:
    """Delete an event from the Realtor Agent calendar."""
    calendar_id = get_or_create_realtor_calendar_id()
    service = _get_service()
    service.events().delete(calendarId=calendar_id, eventId=event_id).execute()

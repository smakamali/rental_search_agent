"""Unit tests for calendar_service.get_available_slots slot-generation algorithm."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from rental_search_agent.calendar_service import get_available_slots


def _make_mock_service(freebusy_response: dict) -> MagicMock:
    """Build a mock Google Calendar service that returns the given freebusy response."""
    mock_freebusy = MagicMock()
    mock_freebusy.query.return_value.execute.return_value = freebusy_response
    mock_service = MagicMock()
    mock_service.freebusy.return_value = mock_freebusy
    return mock_service


def _freebusy_response(busy_primary=None, busy_realtor=None, realtor_id="realtor-cal-id"):
    return {
        "calendars": {
            "primary": {"busy": busy_primary or []},
            realtor_id: {"busy": busy_realtor or []},
        }
    }


REALTOR_ID = "realtor-cal-id"
TZ = "America/Vancouver"

# A Monday 09:00 local time to use as a stable anchor (2026-03-09 is a Monday)
BASE_DATE = "2026-03-09"
BASE_START = f"{BASE_DATE}T00:00:00"
BASE_END = f"2026-03-10T00:00:00"

# Evening range: 2026-03-09 18:00 → 20:00 — gives slots at 18:00 and 19:00
EVENING_START = f"{BASE_DATE}T17:00:00"
EVENING_END = f"{BASE_DATE}T21:00:00"


@pytest.fixture()
def patch_service():
    """Context manager that patches _get_service and get_or_create_realtor_calendar_id."""
    def _ctx(freebusy_resp):
        svc = _make_mock_service(freebusy_resp)
        p1 = patch("rental_search_agent.calendar_service._get_service", return_value=svc)
        p2 = patch(
            "rental_search_agent.calendar_service.get_or_create_realtor_calendar_id",
            return_value=REALTOR_ID,
        )
        return p1, p2

    return _ctx


class TestGetAvailableSlotsHourFilter:
    def test_returns_slots_only_within_preferred_hours(self, patch_service):
        resp = _freebusy_response()
        p1, p2 = patch_service(resp)
        with p1, p2:
            slots = get_available_slots(
                preferred_times="weekday evenings 6-8pm",
                time_min=EVENING_START,
                time_max=EVENING_END,
                timezone=TZ,
            )
        # Only slots at 18:00 and 19:00 should fit (each 1-hour slot must end by 20:00)
        assert len(slots) == 2
        for s in slots:
            h = int(s["start"].split("T")[1].split(":")[0])
            assert 18 <= h < 20

    def test_excludes_slots_outside_preferred_hours(self, patch_service):
        resp = _freebusy_response()
        p1, p2 = patch_service(resp)
        with p1, p2:
            # Range spans a full day but preference is only 9-10am (one slot)
            slots = get_available_slots(
                preferred_times="9-10",
                time_min=f"{BASE_DATE}T08:00:00",
                time_max=f"{BASE_DATE}T12:00:00",
                timezone=TZ,
            )
        assert len(slots) == 1
        assert slots[0]["start"].startswith(f"{BASE_DATE}T09")


class TestGetAvailableSlotsBusyFilter:
    def test_excludes_slots_overlapping_busy_period(self, patch_service):
        # 2026-03-09 is in PDT (UTC-7, DST starts 2026-03-08).
        # 18:00 PDT = 01:00 UTC on 2026-03-10; 19:00 PDT = 02:00 UTC on 2026-03-10.
        busy = [{"start": "2026-03-10T01:00:00Z", "end": "2026-03-10T02:00:00Z"}]
        resp = _freebusy_response(busy_primary=busy)
        p1, p2 = patch_service(resp)
        with p1, p2:
            slots = get_available_slots(
                preferred_times="weekday evenings 6-8pm",
                time_min=EVENING_START,
                time_max=EVENING_END,
                timezone=TZ,
            )
        # The 18:00 slot is busy; only the 19:00 slot remains
        assert len(slots) == 1
        assert "19:00" in slots[0]["start"]

    def test_busy_on_realtor_calendar_also_excluded(self, patch_service):
        # 19:00 PDT = 02:00 UTC on 2026-03-10
        busy = [{"start": "2026-03-10T02:00:00Z", "end": "2026-03-10T03:00:00Z"}]
        resp = _freebusy_response(busy_realtor=busy)
        p1, p2 = patch_service(resp)
        with p1, p2:
            slots = get_available_slots(
                preferred_times="weekday evenings 6-8pm",
                time_min=EVENING_START,
                time_max=EVENING_END,
                timezone=TZ,
            )
        # 19:00 slot is busy; only 18:00 slot should remain
        assert len(slots) == 1
        assert "18:00" in slots[0]["start"]

    def test_returns_empty_when_all_slots_busy(self, patch_service):
        # Mark entire evening range as busy
        busy = [{"start": f"{BASE_DATE}T17:00:00-08:00", "end": f"{BASE_DATE}T21:00:00-08:00"}]
        resp = _freebusy_response(busy_primary=busy)
        p1, p2 = patch_service(resp)
        with p1, p2:
            slots = get_available_slots(
                preferred_times="weekday evenings 6-8pm",
                time_min=EVENING_START,
                time_max=EVENING_END,
                timezone=TZ,
            )
        assert slots == []


class TestGetAvailableSlotsDayFilter:
    def test_weekday_only_excludes_weekend(self, patch_service):
        resp = _freebusy_response()
        p1, p2 = patch_service(resp)
        with p1, p2:
            # Range covers Mon-Sun (2026-03-09 Mon → 2026-03-16 Mon)
            slots = get_available_slots(
                preferred_times="weekdays 9-10",
                time_min="2026-03-09T08:00:00",
                time_max="2026-03-16T10:00:00",
                timezone=TZ,
            )
        # All returned slots should be on Mon-Fri (weekday() < 5)
        for s in slots:
            dt = datetime.fromisoformat(s["start"])
            assert dt.weekday() < 5, f"Expected weekday, got {dt.strftime('%A')} for slot {s['start']}"

    def test_weekend_only_excludes_weekday(self, patch_service):
        resp = _freebusy_response()
        p1, p2 = patch_service(resp)
        with p1, p2:
            # Range covers Mon-Sun (2026-03-09 Mon → 2026-03-16 Mon)
            slots = get_available_slots(
                preferred_times="weekends 10-12",
                time_min="2026-03-09T09:00:00",
                time_max="2026-03-16T12:00:00",
                timezone=TZ,
            )
        # All returned slots should be on Sat(5) or Sun(6)
        for s in slots:
            dt = datetime.fromisoformat(s["start"])
            assert dt.weekday() >= 5, f"Expected weekend, got {dt.strftime('%A')} for slot {s['start']}"


class TestGetAvailableSlotsAllDayDefault:
    def test_no_preferred_times_returns_9_to_17_slots(self, patch_service):
        resp = _freebusy_response()
        p1, p2 = patch_service(resp)
        with p1, p2:
            slots = get_available_slots(
                preferred_times="",
                time_min=f"{BASE_DATE}T08:00:00",
                time_max=f"{BASE_DATE}T18:00:00",
                timezone=TZ,
            )
        # Default is 9-17 → slots at 9,10,11,12,13,14,15,16 = 8 slots
        assert len(slots) == 8
        hours = [int(s["start"].split("T")[1].split(":")[0]) for s in slots]
        assert min(hours) == 9
        assert max(hours) == 16


class TestGetAvailableSlotsErrors:
    def test_raises_value_error_on_calendar_error(self, patch_service):
        resp = {
            "calendars": {
                "primary": {"busy": []},
                REALTOR_ID: {"errors": [{"domain": "calendar", "reason": "notFound"}]},
            }
        }
        p1, p2 = patch_service(resp)
        with p1, p2:
            with pytest.raises(ValueError, match="Calendar access error"):
                get_available_slots(
                    preferred_times="weekday evenings 6-8pm",
                    time_min=EVENING_START,
                    time_max=EVENING_END,
                    timezone=TZ,
                )


class TestGetAvailableSlotsDisplayFormat:
    def test_slot_display_field_is_present(self, patch_service):
        resp = _freebusy_response()
        p1, p2 = patch_service(resp)
        with p1, p2:
            slots = get_available_slots(
                preferred_times="weekday evenings 6-8pm",
                time_min=EVENING_START,
                time_max=EVENING_END,
                timezone=TZ,
            )
        for s in slots:
            assert "display" in s
            assert "start" in s
            assert "end" in s

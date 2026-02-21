"""Unit tests for rental_search_agent.calendar_service.parse_preferred_times."""

import pytest

from rental_search_agent.calendar_service import parse_preferred_times

# Weekday numbers: 0=Mon, 1=Tue, ..., 6=Sun
WEEKDAYS = {0, 1, 2, 3, 4}
WEEKENDS = {5, 6}
ALL_DAYS = set(range(7))


class TestParsePreferredTimes:
    def test_weekday_evenings_6_8pm(self):
        days, start, end = parse_preferred_times("weekday evenings 6–8pm")
        assert days == WEEKDAYS
        assert start == 18
        assert end == 20

    def test_weekends_10am_2pm(self):
        days, start, end = parse_preferred_times("weekends 10am-2pm")
        assert days == WEEKENDS
        assert start == 10
        assert end == 14

    def test_empty_returns_default(self):
        days, start, end = parse_preferred_times("")
        assert days == ALL_DAYS
        assert start == 9
        assert end == 17

    def test_none_returns_default(self):
        days, start, end = parse_preferred_times(None)
        assert days == ALL_DAYS
        assert start == 9
        assert end == 17

    def test_unparseable_returns_default(self):
        days, start, end = parse_preferred_times("whenever you have time")
        assert days == ALL_DAYS
        assert start == 9
        assert end == 17

    def test_evening_keyword(self):
        days, start, end = parse_preferred_times("evenings")
        assert start == 18
        assert end == 20

    def test_morning_keyword(self):
        days, start, end = parse_preferred_times("mornings")
        assert start == 9
        assert end == 12

    @pytest.mark.parametrize(
        "input_str, expected_start, expected_end",
        [
            ("18:00-20:15", 18, 20),
            ("9-17", 9, 17),
        ],
    )
    def test_explicit_time_ranges(self, input_str, expected_start, expected_end):
        days, start, end = parse_preferred_times(input_str)
        assert days == ALL_DAYS
        assert start == expected_start
        assert end == expected_end

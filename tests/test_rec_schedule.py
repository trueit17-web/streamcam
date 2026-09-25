from datetime import datetime, timezone

import pytest

from streemcam.config import RecordingConfig
from streemcam.recording.schedule import (is_recording_time, local_now, parse_hhmm,
                                          seconds_until_change)

REC = RecordingConfig()  # Europe/Moscow = UTC+3, 08:00–19:00


def utc(h, m=0, s=0):
    return datetime(2026, 9, 25, h, m, s, tzinfo=timezone.utc)


def test_parse_hhmm():
    assert parse_hhmm("08:30").hour == 8 and parse_hhmm("08:30").minute == 30


@pytest.mark.parametrize("now,expected", [
    (utc(4, 59), False),   # 07:59 MSK
    (utc(5, 0), True),     # 08:00 MSK
    (utc(15, 59), True),   # 18:59 MSK
    (utc(16, 0), False),   # 19:00 MSK
    (utc(21, 0), False),   # 00:00 MSK
])
def test_is_recording_time(now, expected):
    assert is_recording_time(now, REC) is expected


def test_other_timezone():
    rec = RecordingConfig(timezone="UTC")
    assert is_recording_time(utc(8, 0), rec) is True
    assert is_recording_time(utc(5, 0), rec) is False


def test_local_now():
    assert local_now(utc(5, 0), REC).hour == 8


def test_seconds_until_change():
    assert seconds_until_change(utc(4, 59, 30), REC) == 30          # до начала
    assert seconds_until_change(utc(5, 0), REC) == 11 * 3600        # до конца
    assert seconds_until_change(utc(16, 0), REC) == 13 * 3600       # до завтрашнего начала

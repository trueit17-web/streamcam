from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from ..config import RecordingConfig


def parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def local_now(now: datetime, rec: RecordingConfig) -> datetime:
    return now.astimezone(ZoneInfo(rec.timezone))


def is_recording_time(now: datetime, rec: RecordingConfig) -> bool:
    t = local_now(now, rec).time()
    return parse_hhmm(rec.start) <= t < parse_hhmm(rec.end)


def seconds_until_change(now: datetime, rec: RecordingConfig) -> float:
    local = local_now(now, rec)
    start = local.replace(hour=parse_hhmm(rec.start).hour, minute=parse_hhmm(rec.start).minute,
                          second=0, microsecond=0)
    end = local.replace(hour=parse_hhmm(rec.end).hour, minute=parse_hhmm(rec.end).minute,
                        second=0, microsecond=0)
    if local < start:
        target = start
    elif local < end:
        target = end
    else:
        target = start + timedelta(days=1)
    return (target - local).total_seconds()

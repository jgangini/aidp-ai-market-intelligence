"""Resolve relative publication days with installed IANA data and an explicit clock."""

from datetime import datetime, time, timedelta, timezone
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from zoneinfo import TZPATH, ZoneInfo, ZoneInfoNotFoundError


@lru_cache(maxsize=1)
def country_zones():
    for root in TZPATH:
        try:
            table = (Path(root) / "zone.tab").read_text(encoding="utf-8")
            break
        except OSError:
            continue
    else:
        try:
            table = files("tzdata").joinpath("zoneinfo", "zone.tab").read_text(encoding="utf-8")
        except (ImportError, OSError):
            return {}
    zones = {}
    for line in table.splitlines():
        fields = line.split("\t")
        if line.startswith("#") or len(fields) < 3:
            continue
        zones.setdefault(fields[0], set()).add(fields[2])
    return zones


def relative_day_window(now, day, zone_name=None, country_code=None):
    if now.tzinfo is None or now.utcoffset() is None or day not in ("today", "yesterday"):
        raise ValueError("A zoned clock and a supported relative publication day are required")
    if (zone_name is not None and not isinstance(zone_name, str)
            or country_code is not None and not isinstance(country_code, str)):
        raise ValueError("Calendar timezone and country selectors must be strings or null")
    if zone_name is None:
        choices = country_zones().get(country_code, set())
        zone_name = next(iter(choices)) if len(choices) == 1 else None
    if not zone_name:
        return None
    try:
        zone = ZoneInfo(zone_name)
    except (ValueError, ZoneInfoNotFoundError):
        return None
    local_date = now.astimezone(zone).date() - timedelta(days=day == "yesterday")
    start = datetime.combine(local_date, time.min, tzinfo=zone).astimezone(timezone.utc)
    following = datetime.combine(local_date + timedelta(days=1), time.min, tzinfo=zone)
    end = min(now.astimezone(timezone.utc), following.astimezone(timezone.utc) - timedelta(microseconds=1))
    return {"start": start.isoformat(), "end": end.isoformat(), "publication_timezone": zone.key}

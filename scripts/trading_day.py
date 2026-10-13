"""Offline TWSE calendar gate. No market/calendar HTTP requests at runtime."""
import json
import os
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


TAIPEI = ZoneInfo("Asia/Taipei")
CALENDAR_DIR = Path(__file__).resolve().parents[1] / "data" / "trading_calendar"
# Conservative readiness boundary shared with the existing 21:05 main schedule.
# Market close alone does not mean institutional and margin data are complete.
COMPLETE_DATA_AFTER = time(21, 5)


class CalendarError(ValueError):
    """Missing/unverified calendar data must stop the workflow before HTTP."""


def load_calendar(year, directory=CALENDAR_DIR):
    path = Path(directory) / f"twse-{year}.json"
    try:
        calendar = json.loads(path.read_text(encoding="utf-8"))
        if (calendar["schema_version"] != 1 or calendar["year"] != year
                or calendar["coverage"] != [f"{year}-01-01", f"{year}-12-31"]
                or not calendar["sources"] or not calendar["entries"]):
            raise ValueError("incomplete annual calendar")
        date.fromisoformat(calendar["verified_on"])
        days = {}
        for row in calendar["entries"]:
            day = date.fromisoformat(row["date"])
            if (day.year != year or day.isoformat() != row["date"]
                    or row["date"] in days or not row["name"]
                    or type(row["open"]) is not bool):
                raise ValueError("invalid/duplicate calendar entry")
            days[row["date"]] = row
        return days
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CalendarError(
            f"TWSE calendar {year} missing or invalid: {path}. "
            "Verify the official annual schedule and commit the snapshot before retrying."
        ) from exc


def decision(now, event_name, backfill=False, directory=CALENDAR_DIR):
    """Resolve a scheduled target from completed sessions, entirely offline.

    Manual backfill only bypasses a known closed date; it never bypasses missing
    annual coverage, cooldown, freshness, or quote validation.
    """
    if now.tzinfo is None:
        raise CalendarError("A timezone-aware execution timestamp is required")
    if type(backfill) is not bool:
        raise CalendarError("backfill must be a boolean")
    if event_name not in ("schedule", "workflow_dispatch"):
        raise CalendarError(f"Unsupported update event: {event_name}")
    local = now.astimezone(TAIPEI)
    today = local.date()
    days = load_calendar(today.year, directory)
    entry = days.get(today.isoformat())
    is_open = entry["open"] if entry else today.weekday() < 5
    reason = entry["name"] if entry else "weekday" if is_open else "weekend"
    manual_backfill = event_name == "workflow_dispatch" and backfill
    expected = today
    if event_name == "schedule":
        if local.time() < COMPLETE_DATA_AFTER:
            expected -= timedelta(days=1)
        year = today.year
        while True:
            if expected.year != year:
                year = expected.year
                days = load_calendar(year, directory)
            entry = days.get(expected.isoformat())
            if (entry["open"] if entry else expected.weekday() < 5):
                break
            expected -= timedelta(days=1)
    return {
        "date": today.isoformat(),
        "expected_trading_date": expected.isoformat(),
        # Delayed schedules may recover a completed session even on a closed day.
        "skip": event_name != "schedule" and not is_open and not manual_backfill,
        "reason": reason,
        "manual_backfill": manual_backfill,
    }


def main(now=None):
    try:
        raw_backfill = os.environ.get("BACKFILL", "false").lower()
        if raw_backfill not in ("true", "false"):
            raise CalendarError("BACKFILL must be true or false")
        result = decision(now or datetime.now(TAIPEI),
                          os.environ.get("GITHUB_EVENT_NAME", "schedule"),
                          raw_backfill == "true")
    except CalendarError as exc:
        raise SystemExit(f"CALENDAR FAILED: {exc}") from exc
    skip = str(result["skip"]).lower()
    message = (f"[calendar] taipei_date={result['date']} reason={result['reason']} "
               f"expected_trading_date={result['expected_trading_date']} "
               f"manual_backfill={result['manual_backfill']} skip={skip}")
    print(message)
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as stream:
            stream.write(f"skip={skip}\ntaipei_date={result['date']}\n"
                         f"expected_trading_date={result['expected_trading_date']}\n")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as stream:
            stream.write(message + "\n")


if __name__ == "__main__":
    main()


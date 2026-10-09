"""Offline scheduled-run deduplication; never substitutes for MI_INDEX validation."""
import json
import os
from datetime import date
from pathlib import Path


MISSING_MARGIN = (None, "", "資料待補", "待計算")


def complete_snapshot(data, day, require_margin=True):
    """Only reuse a committed, same-day, fully validated 200-stock snapshot."""
    try:
        if date.fromisoformat(day).isoformat() != day:
            return False
        market = data["market"]
        validation = data["validation"]
        stocks = data["stocks"]
        dates = data["source_dates"]
        codes = [str(s["code"]).strip() for s in stocks]
        return (
            market["quote"]["date"] == day
            and data["trust_date"] == day
            and market["margin"]["history"][-1]["date"] == day
            and all(dates[k] == day for k in ("stock_quote", "market_index", "institutional", "margin"))
            and validation["status"] == "passed"
            and validation["trading_date"] == day
            and validation["same_day_sources"] is True
            and validation["stock_quotes_checked"] == 200
            and len(stocks) == len(set(codes)) == 200
            and all(codes)
            and (not require_margin or all(s.get("margin") not in MISSING_MARGIN for s in stocks))
        )
    except (KeyError, TypeError, ValueError, IndexError, AttributeError):
        return False


def require_scheduled_date(generated, expected, event):
    """A scheduled run returning yesterday is an error, not a successful no-op."""
    if event == "schedule":
        try:
            valid = bool(expected) and date.fromisoformat(expected).isoformat() == expected
        except (ValueError, TypeError):
            valid = False
        if not valid or generated != expected:
            raise SystemExit(
                f"SCHEDULE FRESHNESS FAILED: expected={expected} generated={generated}; "
                "keep the published snapshot and retry at the next scheduled check"
            )


def main():
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    expected = os.environ.get("EXPECTED_TRADING_DATE", "")
    skip = False
    if event == "schedule":
        require_scheduled_date(expected, expected, event)
        try:
            data = json.loads(Path("data/dashboard.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        skip = complete_snapshot(data, expected)
    result = str(skip).lower()
    message = f"[schedule] expected={expected} event={event} already_complete={result}"
    print(message)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"skip={result}\n")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(message + "\n")


if __name__ == "__main__":
    main()

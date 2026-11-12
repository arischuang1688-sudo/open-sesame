"""Same-session TWSE P/E values and the dashboard's reference ranking.

P/E is a preference within the existing signal groups. It never changes the
four technical/institutional conditions or the stock universe.
"""
import math
import re


PE_LIMIT = 20.0
CONDITION_KEYS = ("ma", "kd", "trust", "market")


def signal_fields(stock):
    """Keep unknown distinct from False, including pre-migration snapshots.

    cond_count counts confirmed passes only; compare it only when all four
    conditions are known. Never infer priority from a partial pass rate.
    """
    original = stock.get("conditions") or {}
    conditions = {key: original.get(key) if type(original.get(key)) is bool else None
                  for key in CONDITION_KEYS}
    for key, label in (("ma", "month_week"), ("kd", "kd")):
        value = stock.get(label, "") or ""
        if value.startswith("僅前") or value in (
                "尚未計算", "資料待補", "待計算", "資料不足"):
            conditions[key] = None
    known = sum(value is not None for value in conditions.values())
    passed = sum(value is True for value in conditions.values())
    complete = known == len(CONDITION_KEYS)
    priority = passed == len(CONDITION_KEYS) if complete else None
    return {"conditions": conditions, "cond_count": passed,
            "cond_known_count": known, "signals_complete": complete,
            "ranking_group": "complete" if complete else "pending",
            "priority": priority,
            "status": "待評估" if not complete else "優先買入" if priority else "觀察"}


def positive_pe(value):
    """Return a finite, positive P/E; unavailable or non-positive is None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def stock_pe(code, value):
    """Do not mistake an ETF/other security's placeholder for equity P/E."""
    if not re.fullmatch(r"[1-9][0-9]{3}", str(code).strip()):
        return None
    return positive_pe(value)


def valuation_fields(code, value, day):
    pe = stock_pe(code, value)
    return {"pe_ratio": pe, "pe_under_20": pe is not None and pe <= PE_LIMIT,
            "pe_date": day}


def valuation_matches(stock, raw_pe, day):
    """Compare canonical stored values with the same-day official source."""
    expected = valuation_fields(stock.get("code", ""), raw_pe, day)
    if any(key not in stock for key in expected):
        return False
    actual = stock["pe_ratio"]
    if actual is not None and (type(actual) not in (int, float)
                               or not math.isfinite(actual) or actual <= 0):
        return False
    return (actual == expected["pe_ratio"]
            and stock["pe_under_20"] is expected["pe_under_20"]
            and stock["pe_date"] == day
            and stock.get("quote_date") == day)


def _finite_number(value):
    if isinstance(value, bool):
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def ranking_key(stock):
    signals = signal_fields(stock)
    pe = stock_pe(stock.get("code", ""), stock.get("pe_ratio"))
    preferred = (stock.get("pe_under_20") is True
                 and pe is not None and pe <= PE_LIMIT)
    return (0 if signals["signals_complete"] else 1,
            -int(signals["priority"] is True),
            -signals["cond_count"] if signals["signals_complete"] else 0,
            -int(preferred), -_finite_number(stock.get("amount")),
            -_finite_number(stock.get("volume")), str(stock.get("code", "")))


def rank_stocks(stocks):
    """Rank complete signals; list pending stocks separately by P/E/value.

    Group placement is presentation order, not a cross-group investment rank.
    Return new records so a caller's validated quote snapshot is not mutated.
    """
    normalized = []
    for stock in stocks:
        item = {**stock, **signal_fields(stock)}
        for key in ("month_week", "kd", "relative"):
            if str(item.get(key, "")).startswith("僅前"):
                item[key] = "尚未計算"
        normalized.append(item)
    return sorted(normalized, key=ranking_key)


def complete_valuation(data, day):
    """Only reuse a snapshot whose 200 P/E fields have been validated."""
    try:
        stocks = data["stocks"]
        return (data["source_dates"]["pe_ratio"] == day
                and data["validation"]["pe_ratios_checked"] == 200
                and len(stocks) == 200
                and all(valuation_matches(s, s.get("pe_ratio"), day) for s in stocks))
    except (KeyError, TypeError, ValueError, AttributeError):
        return False

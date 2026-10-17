"""Same-session TWSE P/E values and the dashboard's reference ranking.

P/E is a preference within the existing signal groups. It never changes the
four technical/institutional conditions or the stock universe.
"""
import math
import re


PE_LIMIT = 20.0


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
    pe = stock_pe(stock.get("code", ""), stock.get("pe_ratio"))
    preferred = (stock.get("pe_under_20") is True
                 and pe is not None and pe <= PE_LIMIT)
    return (-int(bool(stock.get("priority"))),
            -_finite_number(stock.get("cond_count")),
            -int(preferred), -_finite_number(stock.get("amount")),
            -_finite_number(stock.get("volume")), str(stock.get("code", "")))


def rank_stocks(stocks):
    """Keep signal strength first, then P/E preference and traded value."""
    return sorted(stocks, key=ranking_key)


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

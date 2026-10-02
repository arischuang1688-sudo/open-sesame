"""Shared, fail-closed parser for TWSE MI_INDEX change fields."""
import math
import re


def signed_change(sign, value):
    """Return a signed change, or None for missing/unsupported source data.

    TWSE's MI_INDEX notes define X as 不比價 (not comparable). Its zero
    price-change placeholder stays numeric zero for the existing dashboard
    schema; it does not establish an unchanged comparable previous close.
    """
    if sign is None:
        return None
    try:
        magnitude = abs(float(str(value).replace(',', '')))
    except (ValueError, TypeError):
        return None
    if not math.isfinite(magnitude):
        return None
    sign = re.sub(r'<[^>]*>', '', str(sign)).strip()
    if sign == '+':
        return magnitude
    if sign == '-':
        return -magnitude
    if sign in ('', 'X') and magnitude == 0:
        return 0.0
    return None

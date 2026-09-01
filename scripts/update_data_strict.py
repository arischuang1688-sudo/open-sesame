# -*- coding: utf-8 -*-
"""Strict daily updater.
Uses TWSE MI_INDEX for date-specific all-stock quotes so stock prices are aligned
with the latest market trading day, then reuses the existing analytics pipeline.
"""
import scripts.update_data as u

MI_INDEX_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={date}&type=ALLBUT0999&response=json"


def _latest_market_date():
    rows = u.fetch_market_history()
    if not rows:
        return None
    return rows[-1]["date"]


def _field_index(fields, *names):
    for name in names:
        if name in fields:
            return fields.index(name)
    return None


def fetch_base_stocks_strict():
    trading_date = _latest_market_date()
    if not trading_date:
        print("[error] cannot determine latest TWSE market trading date")
        return None
    ymd = trading_date.replace("-", "")
    resp = u.fetch_json(MI_INDEX_URL.format(date=ymd), timeout=45, retries=3)
    if not resp or resp.get("stat") != "OK":
        print(f"[error] MI_INDEX unavailable for {trading_date}")
        return None

    target = None
    for t in resp.get("tables", []):
        fields = t.get("fields", [])
        if "證券代號" in fields and "證券名稱" in fields and "收盤價" in fields:
            target = t
            break
    if not target:
        print(f"[error] MI_INDEX stock table not found for {trading_date}")
        return None

    fields = target.get("fields", [])
    data = target.get("data", [])
    i_code = _field_index(fields, "證券代號")
    i_name = _field_index(fields, "證券名稱")
    i_close = _field_index(fields, "收盤價")
    i_open = _field_index(fields, "開盤價")
    i_vol = _field_index(fields, "成交股數")
    i_amt = _field_index(fields, "成交金額")
    i_chg = _field_index(fields, "漲跌價差", "漲跌點數")

    required = [i_code, i_name, i_close, i_vol]
    if any(i is None for i in required):
        print(f"[error] MI_INDEX fields incomplete: {fields}")
        return None

    stocks = []
    for row in data:
        try:
            code = str(row[i_code]).strip()
            name = str(row[i_name]).strip()
            close = u.to_float(row[i_close]) or 0
            open_ = u.to_float(row[i_open]) if i_open is not None else None
            volume_shares = u.to_float(row[i_vol]) or 0
            amount = u.to_float(row[i_amt]) if i_amt is not None else None
            change = u.to_float(row[i_chg]) if i_chg is not None else 0
            change = change or 0
            if not code or not name or close <= 0:
                continue
            prev = close - change
            stocks.append({
                "code": code,
                "name": name,
                "close": close,
                "open": open_ or 0,
                "volume": round(volume_shares / 1000, 2),
                "amount": round(amount / 1e8, 2) if amount is not None else None,
                "change": change,
                "change_pct": round(change / prev * 100, 2) if prev > 0 else 0,
                "quote_date": trading_date,
                "quote_source": "TWSE MI_INDEX",
            })
        except Exception:
            continue

    # Prefer actively traded stocks; fall back to volume when amount is unavailable.
    stocks.sort(key=lambda s: ((s.get("amount") or 0), s["volume"]), reverse=True)
    print(f"[info] MI_INDEX strict quote date={trading_date} rows={len(stocks)}")
    return stocks[:u.TOP_N]


# Replace only the base quote source; keep the existing analysis pipeline.
u.fetch_base_stocks = fetch_base_stocks_strict

if __name__ == "__main__":
    u.main()

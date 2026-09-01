# -*- coding: utf-8 -*-
"""AI 台股六條件選股 Dashboard 每日資料更新腳本。

資料來源(皆為 TWSE 公開端點):
  - STOCK_DAY_ALL : 全部個股盤後行情(OpenAPI)
  - T86           : 三大法人買賣超(投信買賣超股數)
  - MI_MARGN      : 融資融券餘額(大盤彙總 + 個股)
  - FMTQIK        : 大盤加權指數每日成交量值與收盤指數
  - STOCK_DAY     : 個股日K(僅對排名前 HISTORY_TOP 檔抓近幾個月,計算均線/KD/均量/相對大盤)

任一來源抓取失敗時不會中斷:對應欄位標示「資料待補」,其餘照常輸出。
"""

import json
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(BASE_DIR, "data", "dashboard.json")
TW_TZ = timezone(timedelta(hours=8))

STOCK_DAY_ALL_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
T86_URL = "https://www.twse.com.tw/rwd/zh/fund/T86?date={date}&selectType=ALLBUT0999&response=json"
MI_MARGN_URL = "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={date}&selectType={sel}&response=json"
FMTQIK_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?date={date}&response=json"
STOCK_DAY_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY?date={date}&stockNo={code}&response=json"

TOP_N = 200
HISTORY_TOP = 30
HISTORY_MONTHS = 3
MARKET_MONTHS = 4
MARGIN_TREND_DAYS = 12
REQUEST_DELAY = 0.4

VOL_SURGE_5 = 1.5
VOL_SURGE_20 = 2.0
MKT_VOL_SURGE = 1.3
MARGIN_FLAG_PCT = 5.0

PENDING = "資料待補"


def fetch_json(url, timeout=30, retries=2):
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            if i == retries:
                print(f"[warn] fetch failed: {url} ({e})")
                return None
            time.sleep(1.5 * (i + 1))
    return None


def to_float(s):
    try:
        return float(str(s).replace(",", "").replace("+", ""))
    except Exception:
        return None


def roc_date(s):
    try:
        parts = str(s).replace(".", "/").split("/")
        y = int(parts[0]) + 1911
        return f"{y:04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"
    except Exception:
        return str(s)


def fetch_base_stocks():
    data = fetch_json(STOCK_DAY_ALL_URL)
    if not data:
        return None
    stocks = []
    for x in data:
        try:
            code = x.get("Code", "")
            name = x.get("Name", "")
            close = to_float(x.get("ClosingPrice", "0")) or 0
            open_ = to_float(x.get("OpeningPrice", "0")) or 0
            volume = (to_float(x.get("TradeVolume", "0")) or 0) / 1000
            change = to_float(x.get("Change", "0")) or 0
            if not code or not name or close <= 0:
                continue
            prev = close - change
            stocks.append({
                "code": code,
                "name": name,
                "close": close,
                "open": open_,
                "volume": round(volume, 2),
                "change": change,
                "change_pct": round(change / prev * 100, 2) if prev > 0 else 0,
            })
        except Exception:
            continue
    stocks.sort(key=lambda s: (s["change"], s["volume"]), reverse=True)
    return stocks[:TOP_N]


def fetch_trust_net():
    today = datetime.now(TW_TZ)
    for back in range(10):
        d = (today - timedelta(days=back)).strftime("%Y%m%d")
        resp = fetch_json(T86_URL.format(date=d))
        time.sleep(REQUEST_DELAY)
        if not resp or resp.get("stat") != "OK" or not resp.get("data"):
            continue
        fields = resp.get("fields", [])
        try:
            idx = fields.index("投信買賣超股數")
        except ValueError:
            continue
        result = {}
        for row in resp["data"]:
            code = str(row[0]).strip()
            v = to_float(row[idx])
            if code and v is not None:
                result[code] = round(v / 1000)
        if result:
            print(f"[info] T86 date={d} rows={len(result)}")
            return result, roc_or_ymd(d)
    return None, None


def roc_or_ymd(ymd):
    return f"{ymd[0:4]}-{ymd[4:6]}-{ymd[6:8]}"


def parse_mi_margn(resp):
    if not resp or resp.get("stat") != "OK":
        return None, None
    summary, per_stock = None, None
    for t in resp.get("tables", []):
        fields = t.get("fields", [])
        data = t.get("data", [])
        if not data:
            continue
        if "股票代號" in fields:
            try:
                i_prev = fields.index("前日餘額")
                i_today = fields.index("今日餘額")
            except ValueError:
                continue
            per_stock = {}
            for row in data:
                code = str(row[0]).strip()
                prev = to_float(row[i_prev])
                cur = to_float(row[i_today])
                if code and prev is not None and cur is not None:
                    per_stock[code] = {"prev": prev, "today": cur}
        else:
            for row in data:
                item = str(row[0])
                if "融資金額" in item:
                    prev = to_float(row[-2])
                    cur = to_float(row[-1])
                    if prev is not None and cur is not None:
                        summary = {
                            "prev": round(prev / 100000, 1),
                            "today": round(cur / 100000, 1),
                        }
    return summary, per_stock


def fetch_margin():
    today = datetime.now(TW_TZ)
    history = []
    per_stock = None
    for back in range(MARGIN_TREND_DAYS):
        d = (today - timedelta(days=back)).strftime("%Y%m%d")
        sel = "ALL" if per_stock is None else "MS"
        resp = fetch_json(MI_MARGN_URL.format(date=d, sel=sel))
        time.sleep(REQUEST_DELAY)
        summary, stocks = parse_mi_margn(resp)
        if stocks and per_stock is None:
            per_stock = stocks
        if summary:
            history.append({"date": roc_or_ymd(d), "balance": summary["today"], "prev_balance": summary["prev"]})
    history.reverse()
    return (history or None), per_stock


def fetch_market_history():
    today = datetime.now(TW_TZ)
    rows = []
    for m in range(MARKET_MONTHS - 1, -1, -1):
        y, mo = today.year, today.month - m
        while mo <= 0:
            y, mo = y - 1, mo + 12
        d = f"{y:04d}{mo:02d}01"
        resp = fetch_json(FMTQIK_URL.format(date=d))
        time.sleep(REQUEST_DELAY)
        if not resp or resp.get("stat") != "OK" or not resp.get("data"):
            continue
        for row in resp["data"]:
            idx = to_float(row[4])
            amt = to_float(row[2])
            chg = to_float(row[5])
            if idx is None or amt is None:
                continue
            rows.append({"date": roc_date(row[0]), "index": idx, "change": chg or 0, "amount": round(amt / 1e8, 1)})
    return rows or None


def fetch_stock_history(code):
    today = datetime.now(TW_TZ)
    rows = []
    for m in range(HISTORY_MONTHS - 1, -1, -1):
        y, mo = today.year, today.month - m
        while mo <= 0:
            y, mo = y - 1, mo + 12
        d = f"{y:04d}{mo:02d}01"
        resp = fetch_json(STOCK_DAY_URL.format(date=d, code=code))
        time.sleep(REQUEST_DELAY)
        if not resp or resp.get("stat") != "OK" or not resp.get("data"):
            continue
        for row in resp["data"]:
            close = to_float(row[6])
            high = to_float(row[4])
            low = to_float(row[5])
            vol = to_float(row[1])
            if close is None or high is None or low is None:
                continue
            rows.append({"date": roc_date(row[0]), "close": close, "high": high, "low": low, "volume": round((vol or 0) / 1000, 2)})
    return rows


def sma(values, n, offset=0):
    end = len(values) - offset
    if end - n < 0:
        return None
    seg = values[end - n:end]
    return sum(seg) / n


def calc_kd(rows, n=9):
    if len(rows) < n + 1:
        return None, None
    ks, ds = [50.0], [50.0]
    for i in range(n - 1, len(rows)):
        window = rows[i - n + 1:i + 1]
        hi = max(r["high"] for r in window)
        lo = min(r["low"] for r in window)
        rsv = 50.0 if hi == lo else (rows[i]["close"] - lo) / (hi - lo) * 100
        ks.append(ks[-1] * 2 / 3 + rsv / 3)
        ds.append(ds[-1] * 2 / 3 + ks[-1] / 3)
    return ks, ds


def trend_up(closes, n):
    ma_now = sma(closes, n)
    ma_prev = sma(closes, n, offset=3)
    if ma_now is None or ma_prev is None:
        return None
    return closes[-1] > ma_now and ma_now > ma_prev


def analyze_stock_history(rows, market_ret20):
    out = {}
    closes = [r["close"] for r in rows]
    vols = [r["volume"] for r in rows]
    m_up = trend_up(closes, 20)
    w_up = trend_up(closes, 5)
    out["month_up"], out["week_up"] = m_up, w_up
    if m_up is None or w_up is None:
        out["month_week"] = "資料不足"
    else:
        out["month_week"] = f"月{'↑' if m_up else '↓'}/週{'↑' if w_up else '↓'}"
    ks, ds = calc_kd(rows)
    if ks is None or len(ks) < 4:
        out["kd"], out["kd_golden"] = "資料不足", None
    else:
        golden = ks[-1] > ds[-1] and any(ks[-i] <= ds[-i] + 0.5 for i in range(2, 5))
        out["k"], out["d"] = round(ks[-1], 1), round(ds[-1], 1)
        out["kd_golden"] = golden
        out["kd"] = (f"K{out['k']}/D{out['d']}" + ("〔黃金交叉〕" if golden else "" if ks[-1] > ds[-1] else "〔K＜D〕"))
    avg5 = sma(vols[:-1], 5)
    avg20 = sma(vols[:-1], 20)
    vol = vols[-1]
    if avg5:
        ratio = vol / avg5
        out["vol_ratio"] = round(ratio, 2)
        out["vol_surge"] = ratio >= VOL_SURGE_5 or (bool(avg20) and vol >= VOL_SURGE_20 * avg20)
    else:
        out["vol_ratio"], out["vol_surge"] = None, None
    if len(closes) >= 21 and closes[-21] > 0:
        ret20 = (closes[-1] / closes[-21] - 1) * 100
        out["ret20_pct"] = round(ret20, 1)
        if market_ret20 is not None:
            rel = ret20 - market_ret20
            out["rel_pct"] = round(rel, 1)
            out["outperform"] = rel > 0
            out["relative"] = f"{ret20:+.1f}% vs 大盤{market_ret20:+.1f}%"
        else:
            out["relative"] = f"20日{ret20:+.1f}%(大盤{PENDING})"
    else:
        out["relative"] = "資料不足"
    return out


def analyze_market(mkt_rows):
    if not mkt_rows:
        return None
    closes = [r["index"] for r in mkt_rows]
    amounts = [r["amount"] for r in mkt_rows]
    last = mkt_rows[-1]
    m = {"date": last["date"], "index": last["index"], "change": last["change"], "change_pct": round(last["change"] / (last["index"] - last["change"]) * 100, 2) if last["index"] != last["change"] else None, "amount": last["amount"]}
    m["ma20"] = round(sma(closes, 20), 0) if sma(closes, 20) else None
    m["ma60"] = round(sma(closes, 60), 0) if sma(closes, 60) else None
    m["trend_up"] = trend_up(closes, 20)
    m["above_ma60"] = (closes[-1] > m["ma60"]) if m["ma60"] else None
    avg5 = sma(amounts[:-1], 5)
    avg20 = sma(amounts[:-1], 20)
    m["amount_avg5"] = round(avg5, 1) if avg5 else None
    m["amount_avg20"] = round(avg20, 1) if avg20 else None
    if avg5:
        m["vol_ratio"] = round(last["amount"] / avg5, 2)
        m["vol_surge"] = m["vol_ratio"] >= MKT_VOL_SURGE
    else:
        m["vol_ratio"], m["vol_surge"] = None, None
    if len(closes) >= 21 and closes[-21] > 0:
        m["ret20_pct"] = round((closes[-1] / closes[-21] - 1) * 100, 1)
    else:
        m["ret20_pct"] = None
    return m


def build_reason(s):
    parts = []
    if s.get("rel_pct") is not None:
        parts.append(f"20日漲幅{s['ret20_pct']:+.1f}%,跑贏大盤 {s['rel_pct']:+.1f} 個百分點")
    if (s.get("trust_net") or 0) > 0:
        parts.append(f"投信買超 {s['trust_net']:,} 張")
    if s.get("vol_surge"):
        parts.append(f"量能放大({s['vol_ratio']} 倍 5 日均量)")
    if s.get("kd_golden"):
        parts.append("KD 黃金交叉")
    if s.get("margin_flag"):
        parts.append(f"融資{s['margin_flag']}")
    return ";".join(parts) if parts else None


def main():
    stocks = fetch_base_stocks()
    if stocks is None:
        print("[warn] STOCK_DAY_ALL unavailable, falling back to existing dashboard.json")
        try:
            with open(OUT_PATH, encoding="utf-8") as f:
                old = json.load(f)
            stocks = [{k: s.get(k) for k in ("code", "name", "close", "volume", "change", "change_pct")} for s in old.get("stocks", [])]
        except Exception:
            stocks = []

    trust, trust_date = fetch_trust_net()
    margin_history, margin_stocks = fetch_margin()
    mkt_rows = fetch_market_history()
    market = analyze_market(mkt_rows)
    market_ret20 = market.get("ret20_pct") if market else None
    market_trend_up = market.get("trend_up") if market else None

    margin_summary = None
    if margin_history:
        latest = margin_history[-1]
        diff = round(latest["balance"] - latest["prev_balance"], 1)
        trend = None
        if len(margin_history) >= 5:
            d5 = latest["balance"] - margin_history[-5]["balance"]
            trend = "增" if d5 > 0 else "減" if d5 < 0 else "平"
        margin_summary = {"balance": latest["balance"], "change": diff, "trend": trend, "history": margin_history}

    for i, s in enumerate(stocks):
        s["trust_net"] = trust.get(s["code"], 0) if trust else None
        if margin_stocks and s["code"] in margin_stocks:
            ms = margin_stocks[s["code"]]
            s["margin_balance"] = ms["today"]
            if ms["prev"] > 0:
                pct = (ms["today"] - ms["prev"]) / ms["prev"] * 100
                s["margin_change_pct"] = round(pct, 1)
                s["margin_flag"] = ("大增" if pct >= MARGIN_FLAG_PCT else "大減" if pct <= -MARGIN_FLAG_PCT else None)
                s["margin"] = f"{ms['today']:,.0f} 張({pct:+.1f}%)"
            else:
                s["margin"] = f"{ms['today']:,.0f} 張"
        elif margin_stocks:
            s["margin"] = "無融資"
        else:
            s["margin"] = PENDING

        if i < HISTORY_TOP:
            rows = fetch_stock_history(s["code"])
            if rows and len(rows) >= 6:
                s.update(analyze_stock_history(rows, market_ret20))
            else:
                s["month_week"] = s["kd"] = s["relative"] = PENDING
        else:
            s["month_week"] = s["kd"] = s["relative"] = f"僅前{HISTORY_TOP}名計算"

        conds = {"ma": bool(s.get("month_up")) and bool(s.get("week_up")), "kd": bool(s.get("kd_golden")), "trust": bool(s.get("trust_net") and s["trust_net"] > 0), "market": bool(market_trend_up)}
        s["conditions"] = conds
        s["cond_count"] = sum(conds.values())
        s["priority"] = all(conds.values())
        s["status"] = "優先買入" if s["priority"] else "觀察"
        if s.get("outperform"):
            s["reason"] = build_reason(s)
            s["news"] = PENDING
        else:
            s["reason"] = None

    if trust is None:
        print("[warn] T86 unavailable → 投信欄位標示待補")
    result = {
        "updated_at": datetime.now(TW_TZ).isoformat(),
        "trust_date": trust_date,
        "market": {
            "source": "TWSE",
            "finance_today": margin_summary["balance"] * 100 if margin_summary else None,
            "margin": margin_summary,
            "quote": market,
        },
        "stocks": stocks,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    n_pri = sum(1 for s in stocks if s.get("priority"))
    print(f"[done] stocks={len(stocks)} priority={n_pri} market={'ok' if market else PENDING}")


if __name__ == "__main__":
    main()

"""
選股雷達 — Python Flask 後端 v4
台股資料來源：
  - 上市（TSE）：mis.twse.com.tw + twse.com.tw
  - 上櫃（OTC）：mis.twse.com.tw + tpex.org.tw
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import traceback
import time
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)
CORS(app)

# ===== API 端點 =====
MIS_URL     = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
TSE_HIST    = "https://www.twse.com.tw/exchangeReport/STOCK_DAY"
TSE_PE      = "https://www.twse.com.tw/exchangeReport/BWIBBU_d"
OTC_HIST    = "https://www.tpex.org.tw/web/stock/aftertrading/daily_trading_info/st43_result.php"
OTC_PE      = "https://www.tpex.org.tw/web/stock/aftertrading/peratio_analysis/pera_result.php"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://mis.twse.com.tw/",
}

# ===== 工具函式 =====

def safe_val(val, decimals=2):
    if val is None or str(val).strip() in ("", "-", "N/A", "nan", "--"):
        return None
    try:
        f = float(str(val).replace(",", "").replace("+", ""))
        return None if (np.isnan(f) or np.isinf(f)) else round(f, decimals)
    except Exception:
        return None

# ===== 判斷上市或上櫃 =====

def detect_market(code):
    """
    透過 MIS API 同時試查上市(tse)與上櫃(otc)，
    回傳 ("tse"/"otc", 即時資料 dict) 或 (None, None)
    """
    for prefix in ["tse", "otc"]:
        try:
            res = requests.get(MIS_URL,
                params={"ex_ch": f"{prefix}_{code}.tw", "json": 1, "delay": 0},
                headers=HEADERS, timeout=10, verify=False)
            msg = res.json().get("msgArray", [])
            if msg and msg[0].get("n"):   # 有股票名稱就代表找到了
                s = msg[0]
                price  = safe_val(s.get("z")) or safe_val(s.get("y"))  # z=成交, y=昨收
                prev   = safe_val(s.get("y"))
                change = round((price - prev) / prev * 100, 2) if price and prev and prev != 0 else None
                return prefix, {
                    "code": code, "name": s.get("n", code),
                    "price": price, "prev_close": prev, "change": change,
                    "open": safe_val(s.get("o")), "high": safe_val(s.get("h")),
                    "low": safe_val(s.get("l")), "volume": safe_val(s.get("v"), 0),
                }
        except Exception as e:
            print(f"[MIS {prefix}] {code}: {e}")
        time.sleep(0.2)
    return None, None

# ===== 上市歷史資料（TWSE）=====

def get_tse_history(code, months=5):
    all_c, all_h, all_l = [], [], []
    today = datetime.now()
    for i in range(months):
        d = (today - timedelta(days=30 * i)).strftime("%Y%m01")
        try:
            res = requests.get(TSE_HIST,
                params={"response": "json", "date": d, "stockNo": code},
                headers=HEADERS, timeout=10, verify=False)
            data = res.json()
            if data.get("stat") == "OK":
                for row in (data.get("data") or []):
                    c = safe_val(row[6]); h = safe_val(row[4]); l = safe_val(row[5])
                    if c: all_c.append(c); all_h.append(h or c); all_l.append(l or c)
            time.sleep(0.35)
        except Exception as e:
            print(f"[TSE hist] {code} {d}: {e}")
    all_c.reverse(); all_h.reverse(); all_l.reverse()
    return all_c, all_h, all_l

# ===== 上櫃歷史資料（TPEx）=====

def get_otc_history(code, months=5):
    all_c, all_h, all_l = [], [], []
    today = datetime.now()
    for i in range(months):
        target = today - timedelta(days=30 * i)
        # TPEx 使用民國年
        roc_year = target.year - 1911
        date_str = f"{roc_year}/{target.month:02d}"
        try:
            res = requests.get(OTC_HIST,
                params={"d": date_str, "stkno": code, "s": "0,asc,0", "l": "zh-tw", "o": "json"},
                headers={**HEADERS, "Referer": "https://www.tpex.org.tw/"},
                timeout=10, verify=False)
            data = res.json()
            rows = data.get("aaData") or data.get("data") or []
            for row in rows:
                # TPEx 欄位：日期、成交張數、成交金額、開盤、最高、最低、收盤、漲跌、筆數
                c = safe_val(row[6]); h = safe_val(row[4]); l = safe_val(row[5])
                if c: all_c.append(c); all_h.append(h or c); all_l.append(l or c)
            time.sleep(0.35)
        except Exception as e:
            print(f"[OTC hist] {code} {date_str}: {e}")
    all_c.reverse(); all_h.reverse(); all_l.reverse()
    return all_c, all_h, all_l

# ===== 上市本益比殖利率（TWSE）=====

def get_tse_pe(code, months=5):
    today = datetime.now()
    for i in range(months):
        d = (today - timedelta(days=30 * i)).strftime("%Y%m01")
        try:
            res = requests.get(TSE_PE,
                params={"response": "json", "date": d, "stockNo": code},
                headers=HEADERS, timeout=10, verify=False)
            data = res.json()
            if data.get("stat") == "OK":
                for row in reversed(data.get("data") or []):
                    pe = safe_val(row[3]); yld = safe_val(row[1]); pb = safe_val(row[5])
                    if pe or yld:
                        return {"pe": pe, "yield_pct": yld, "pb": pb}
            time.sleep(0.3)
        except Exception as e:
            print(f"[TSE PE] {code}: {e}")
    return {}

# ===== 上櫃本益比殖利率（TPEx）=====

def get_otc_pe(code, months=5):
    today = datetime.now()
    for i in range(months):
        target = today - timedelta(days=30 * i)
        roc_year = target.year - 1911
        date_str = f"{roc_year}/{target.month:02d}"
        try:
            res = requests.get(OTC_PE,
                params={"d": date_str, "stkno": code, "l": "zh-tw", "o": "json"},
                headers={**HEADERS, "Referer": "https://www.tpex.org.tw/"},
                timeout=10, verify=False)
            data = res.json()
            rows = data.get("aaData") or data.get("data") or []
            for row in reversed(rows):
                # TPEx PE 欄位：日期、本益比、股利、殖利率、淨值比
                pe  = safe_val(row[1])
                yld = safe_val(row[3])
                pb  = safe_val(row[4])
                if pe or yld:
                    return {"pe": pe, "yield_pct": yld, "pb": pb}
            time.sleep(0.3)
        except Exception as e:
            print(f"[OTC PE] {code}: {e}")
    return {}

# ===== 技術指標 =====

def calc_rsi(prices, period=14):
    if len(prices) < period + 1: return None
    s = pd.Series(prices)
    d = s.diff()
    gain = d.clip(lower=0).rolling(period).mean()
    loss = -d.clip(upper=0).rolling(period).mean()
    rs = gain / loss
    return safe_val((100 - 100 / (1 + rs)).iloc[-1], 1)

def calc_macd(prices):
    if len(prices) < 26: return "unknown"
    s = pd.Series(prices)
    macd   = s.ewm(span=12, adjust=False).mean() - s.ewm(span=26, adjust=False).mean()
    signal = macd.ewm(span=9, adjust=False).mean()
    hist   = macd - signal
    if macd.iloc[-2] < signal.iloc[-2] and macd.iloc[-1] > signal.iloc[-1]: return "bullish"
    if macd.iloc[-2] > signal.iloc[-2] and macd.iloc[-1] < signal.iloc[-1]: return "bearish"
    return "positive" if hist.iloc[-1] > 0 else "negative"

def calc_kd(h, l, c, period=9):
    if len(c) < period: return None, None
    lmin = pd.Series(l).rolling(period).min()
    hmax = pd.Series(h).rolling(period).max()
    diff = (hmax - lmin).replace(0, np.nan)
    rsv  = (pd.Series(c) - lmin) / diff * 100
    k = rsv.ewm(com=2, adjust=False).mean()
    d = k.ewm(com=2, adjust=False).mean()
    return safe_val(k.iloc[-1], 1), safe_val(d.iloc[-1], 1)

def get_ma_state(prices):
    if len(prices) < 20: return "unknown"
    s = pd.Series(prices)
    cur = s.iloc[-1]
    ma5  = s.rolling(5).mean().iloc[-1]
    ma20 = s.rolling(20).mean().iloc[-1]
    if len(prices) >= 60:
        ma60 = s.rolling(60).mean().iloc[-1]
        if cur > ma5 > ma20 > ma60: return "all_above"
    if ma5 > ma20 and cur > ma20: return "golden_cross"
    if cur > ma20: return "above_ma20"
    if len(prices) >= 60 and cur > s.rolling(60).mean().iloc[-1]: return "above_ma60"
    return "below_all"

def gen_signals(pe, yield_pct, rsi, macd, ma_state):
    s = []
    if macd == "bullish":            s.append("MACD黃金交叉")
    if ma_state == "all_above":      s.append("多頭排列")
    elif ma_state == "golden_cross": s.append("均線交叉")
    if rsi and rsi < 30:             s.append("RSI超賣")
    if yield_pct and yield_pct >= 4: s.append("高殖利率")
    if pe and pe < 12:               s.append("低本益比")
    if rsi and 50 < rsi < 70:        s.append("強勢區間")
    return s[:4]

# ===== API 端點 =====

@app.route("/")
def index():
    return jsonify({"status": "ok", "message": "選股雷達後端 v4（支援上市+上櫃）", "time": datetime.now().isoformat()})

@app.route("/api/stock/<code>")
def get_stock(code):
    code = code.strip().upper().replace(".TW", "").replace(".TWO", "")
    try:
        # 1. 偵測上市/上櫃並取得即時報價
        market_type, realtime = detect_market(code)

        # 2. 取得歷史資料
        if market_type == "otc":
            close_list, high_list, low_list = get_otc_history(code)
        else:
            close_list, high_list, low_list = get_tse_history(code)

        if not close_list:
            return jsonify({"error": f"找不到股票：{code}，請確認是否為台股代號"}), 404

        # 3. 即時報價查不到時用歷史資料補
        if not realtime:
            price  = close_list[-1]
            prev   = close_list[-2] if len(close_list) >= 2 else price
            change = round((price - prev) / prev * 100, 2) if prev else None
            realtime = {"code": code, "name": code, "price": price,
                        "prev_close": prev, "change": change,
                        "open": None, "high": None, "low": None, "volume": None}

        # 4. 本益比殖利率
        time.sleep(0.3)
        pe_data = get_otc_pe(code) if market_type == "otc" else get_tse_pe(code)
        pe        = pe_data.get("pe")
        yield_pct = pe_data.get("yield_pct")

        # 5. 技術指標
        rsi      = calc_rsi(close_list) if len(close_list) >= 15 else None
        macd_sig = calc_macd(close_list) if len(close_list) >= 26 else "unknown"
        k_val, d_val = calc_kd(high_list, low_list, close_list)
        ma_state = get_ma_state(close_list)
        recent_30 = close_list[-30:]
        high52 = max(high_list) if high_list else None
        low52  = min(low_list)  if low_list  else None

        return jsonify({
            "code": code, "name": realtime["name"],
            "market": "tw", "market_type": market_type or "tse",
            "price": realtime["price"], "prev_close": realtime.get("prev_close"),
            "change": realtime.get("change"),
            "day_high": realtime.get("high"), "day_low": realtime.get("low"),
            "open": realtime.get("open"), "volume": realtime.get("volume"),
            "week_52_high": high52, "week_52_low": low52,
            "pe": pe, "pb": pe_data.get("pb"), "yield_pct": yield_pct,
            "roe": None, "eps_growth": None, "gross": None, "debt": None, "cap": "large",
            "rsi": rsi, "macd": macd_sig, "kd": k_val, "kd_d": d_val, "ma_state": ma_state,
            "foreign_days": None, "invest": None, "margin_pct": None,
            "price_history": recent_30,
            "signals": gen_signals(pe, yield_pct, rsi, macd_sig, ma_state),
            "sector": None, "currency": "TWD",
            "updated_at": datetime.now().isoformat()
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route("/api/screen")
def screen_stocks():
    codes_param = request.args.get("codes", "")
    if not codes_param:
        return jsonify({"error": "請提供股票代號列表"}), 400
    codes = [c.strip().replace(".TW","").replace(".TWO","").upper()
             for c in codes_param.split(",") if c.strip()]
    if len(codes) > 30:
        return jsonify({"error": "單次最多查詢 30 檔"}), 400

    results, errors = [], []
    for code in codes:
        try:
            market_type, realtime = detect_market(code)
            if market_type == "otc":
                close_list, high_list, low_list = get_otc_history(code)
            else:
                close_list, high_list, low_list = get_tse_history(code)
            if not close_list:
                errors.append(code); continue
            if not realtime:
                price = close_list[-1]; prev = close_list[-2] if len(close_list)>=2 else price
                realtime = {"code": code, "name": code, "price": price,
                            "change": round((price-prev)/prev*100,2) if prev else None}
            time.sleep(0.3)
            pe_data   = get_otc_pe(code) if market_type=="otc" else get_tse_pe(code)
            pe        = pe_data.get("pe")
            yield_pct = pe_data.get("yield_pct")
            rsi      = calc_rsi(close_list) if len(close_list)>=15 else None
            macd_sig = calc_macd(close_list) if len(close_list)>=26 else "unknown"
            k_val, _ = calc_kd(high_list, low_list, close_list)
            ma_state = get_ma_state(close_list)
            results.append({
                "code": code, "name": realtime["name"], "market": "tw",
                "price": realtime["price"], "change": realtime.get("change"),
                "pe": pe, "yield_pct": yield_pct, "roe": None, "gross": None, "debt": None,
                "rsi": rsi, "macd": macd_sig, "kd": k_val, "ma_state": ma_state,
                "cap": "large", "foreign_days": None, "invest": None, "margin_pct": None,
                "signals": gen_signals(pe, yield_pct, rsi, macd_sig, ma_state),
            })
            time.sleep(0.5)
        except Exception as e:
            errors.append(code); print(f"[ERROR] {code}: {e}")

    return jsonify({"results": results, "total": len(results),
                    "errors": errors, "updated_at": datetime.now().isoformat()})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)

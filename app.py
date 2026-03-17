"""
選股雷達 — Python Flask 後端 v3
台股資料來源：台灣證券交易所 TWSE 官方 API
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

# --- 請在這裡補上這段「首頁」程式碼 ---
@app.route('/')
def home():
    return "選股雷達後端正常運作中！"
# -----------------------------------

MIS_URL      = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
HISTORY_URL  = "https://www.twse.com.tw/exchangeReport/STOCK_DAY"
PE_URL       = "https://www.twse.com.tw/exchangeReport/BWIBBU_d"
WEEK52_URL   = "https://www.twse.com.tw/exchangeReport/STOCK_DAY_AVG"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://mis.twse.com.tw/",
}

# ===== 工具函式 =====

def safe_val(val, decimals=2):
    if val is None or str(val).strip() in ("", "-", "N/A", "nan"):
        return None
    try:
        f = float(str(val).replace(",", "").replace("+", ""))
        return None if (np.isnan(f) or np.isinf(f)) else round(f, decimals)
    except Exception:
        return None

# ===== 即時報價 =====

def get_realtime(code):
    for prefix in ["tse", "otc"]:
        try:
            res = requests.get(MIS_URL,
                params={"ex_ch": f"{prefix}_{code}.tw", "json": 1, "delay": 0},
                headers=HEADERS, timeout=10, verify=False)
            msg = res.json().get("msgArray", [])
            if msg and msg[0].get("z") not in (None, "-", ""):
                s = msg[0]
                price = safe_val(s.get("z"))
                prev  = safe_val(s.get("y"))
                change = round((price - prev) / prev * 100, 2) if price and prev and prev != 0 else None
                return {"code": code, "name": s.get("n", code),
                        "price": price, "prev_close": prev, "change": change,
                        "open": safe_val(s.get("o")), "high": safe_val(s.get("h")),
                        "low": safe_val(s.get("l")), "volume": safe_val(s.get("v"), 0),
                        "market_type": prefix}
        except Exception as e:
            print(f"[MIS error] {prefix}_{code}: {e}")
    return None

# ===== 歷史收盤價（取近 5 個月，確保足夠計算 MA60）=====

def get_history(code):
    all_close, all_high, all_low = [], [], []
    today = datetime.now()
    for i in range(5):  # 取近 5 個月，確保 MA60 有足夠資料
        target = today - timedelta(days=30 * i)
        date_str = target.strftime("%Y%m01")
        try:
            res = requests.get(HISTORY_URL,
                params={"response": "json", "date": date_str, "stockNo": code},
                headers=HEADERS, timeout=10, verify=False)
            data = res.json()
            if data.get("stat") == "OK" and data.get("data"):
                for row in data["data"]:
                    # 欄位順序：日期、成交股數、成交金額、開盤、最高、最低、收盤、漲跌、筆數
                    c = safe_val(row[6])
                    h = safe_val(row[4])
                    l = safe_val(row[5])
                    if c:
                        all_close.append(c)
                        all_high.append(h or c)
                        all_low.append(l or c)
            time.sleep(0.35)
        except Exception as e:
            print(f"[History error] {code} {date_str}: {e}")

    all_close.reverse()
    all_high.reverse()
    all_low.reverse()
    return all_close, all_high, all_low

# ===== 本益比、殖利率（嘗試多個日期）=====

def get_pe_yield(code):
    today = datetime.now()
    for i in range(5):
        target = today - timedelta(days=30 * i)
        date_str = target.strftime("%Y%m01")
        try:
            res = requests.get(PE_URL,
                params={"response": "json", "date": date_str, "stockNo": code},
                headers=HEADERS, timeout=10, verify=False)
            data = res.json()
            if data.get("stat") == "OK" and data.get("data"):
                # 找最後一筆有效資料
                for row in reversed(data["data"]):
                    pe  = safe_val(row[3])
                    yld = safe_val(row[1])
                    pb  = safe_val(row[5])
                    if pe or yld:
                        return {"pe": pe, "yield_pct": yld, "pb": pb}
            time.sleep(0.3)
        except Exception as e:
            print(f"[PE error] {code} {date_str}: {e}")
    return {}

# ===== 52 週高低點 =====

def get_52w(code):
    high52, low52 = None, None
    today = datetime.now()
    all_h, all_l = [], []
    for i in range(12):  # 取近 12 個月
        target = today - timedelta(days=30 * i)
        date_str = target.strftime("%Y%m01")
        try:
            res = requests.get(HISTORY_URL,
                params={"response": "json", "date": date_str, "stockNo": code},
                headers=HEADERS, timeout=10, verify=False)
            data = res.json()
            if data.get("stat") == "OK" and data.get("data"):
                for row in data["data"]:
                    h = safe_val(row[4])
                    l = safe_val(row[5])
                    if h: all_h.append(h)
                    if l: all_l.append(l)
            time.sleep(0.2)
        except Exception:
            pass
    if all_h: high52 = max(all_h)
    if all_l: low52  = min(all_l)
    return high52, low52

# ===== 技術指標 =====

def calc_rsi(prices, period=14):
    if len(prices) < period + 1:
        return None
    s = pd.Series(prices)
    d = s.diff()
    gain = d.clip(lower=0).rolling(period).mean()
    loss = -d.clip(upper=0).rolling(period).mean()
    rs = gain / loss
    return safe_val((100 - 100 / (1 + rs)).iloc[-1], 1)

def calc_macd(prices):
    if len(prices) < 26:
        return "unknown"
    s = pd.Series(prices)
    macd   = s.ewm(span=12, adjust=False).mean() - s.ewm(span=26, adjust=False).mean()
    signal = macd.ewm(span=9, adjust=False).mean()
    hist   = macd - signal
    if macd.iloc[-2] < signal.iloc[-2] and macd.iloc[-1] > signal.iloc[-1]:
        return "bullish"
    if macd.iloc[-2] > signal.iloc[-2] and macd.iloc[-1] < signal.iloc[-1]:
        return "bearish"
    return "positive" if hist.iloc[-1] > 0 else "negative"

def calc_kd(h_list, l_list, c_list, period=9):
    if len(c_list) < period:
        return None, None
    lmin = pd.Series(l_list).rolling(period).min()
    hmax = pd.Series(h_list).rolling(period).max()
    diff = (hmax - lmin).replace(0, np.nan)
    rsv  = (pd.Series(c_list) - lmin) / diff * 100
    k = rsv.ewm(com=2, adjust=False).mean()
    d = k.ewm(com=2, adjust=False).mean()
    return safe_val(k.iloc[-1], 1), safe_val(d.iloc[-1], 1)

def get_ma_state(prices):
    if len(prices) < 60:
        # 資料不足 60 筆，改用 MA20 判斷
        if len(prices) < 20:
            return "unknown"
        s = pd.Series(prices)
        ma5  = s.rolling(5).mean().iloc[-1]
        ma20 = s.rolling(20).mean().iloc[-1]
        cur  = s.iloc[-1]
        if ma5 > ma20 and cur > ma20:
            return "golden_cross"
        return "above_ma20" if cur > ma20 else "below_all"
    s = pd.Series(prices)
    ma5  = s.rolling(5).mean().iloc[-1]
    ma20 = s.rolling(20).mean().iloc[-1]
    ma60 = s.rolling(60).mean().iloc[-1]
    cur  = s.iloc[-1]
    if cur > ma5 > ma20 > ma60: return "all_above"
    if ma5 > ma20:              return "golden_cross"
    if cur > ma20:              return "above_ma20"
    if cur > ma60:              return "above_ma60"
    return "below_all"

def gen_signals(pe, yield_pct, rsi, macd, ma_state):
    s = []
    if macd == "bullish":          s.append("MACD黃金交叉")
    if ma_state == "all_above":    s.append("多頭排列")
    elif ma_state == "golden_cross": s.append("均線交叉")
    if rsi and rsi < 30:           s.append("RSI超賣")
    if yield_pct and yield_pct >= 4: s.append("高殖利率")
    if pe and pe < 12:             s.append("低本益比")
    if rsi and 50 < rsi < 70:      s.append("強勢區間")
    return s[:4]

# ===== API 端點 =====

@app.route("/")
def index():
    return jsonify({"status": "ok", "message": "選股雷達後端運作中 v3", "time": datetime.now().isoformat()})

@app.route("/api/stock/<code>")
def get_stock(code):
    code = code.strip().upper().replace(".TW", "")
    try:
        realtime = get_realtime(code)
        close_list, high_list, low_list = get_history(code)

        if not close_list:
            return jsonify({"error": f"找不到股票：{code}，請確認代號是否為台股上市/上櫃代號"}), 404

        if not realtime:
            price  = close_list[-1]
            prev   = close_list[-2] if len(close_list) >= 2 else price
            change = round((price - prev) / prev * 100, 2) if prev != 0 else None
            realtime = {"code": code, "name": code, "price": price,
                        "prev_close": prev, "change": change,
                        "open": None, "high": None, "low": None, "volume": None}

        time.sleep(0.3)
        pe_data   = get_pe_yield(code)
        pe        = pe_data.get("pe")
        yield_pct = pe_data.get("yield_pct")

        rsi      = calc_rsi(close_list) if len(close_list) >= 15 else None
        macd_sig = calc_macd(close_list) if len(close_list) >= 26 else "unknown"
        k_val, d_val = calc_kd(high_list, low_list, close_list)
        ma_state = get_ma_state(close_list)
        recent_30 = close_list[-30:] if len(close_list) >= 30 else close_list

        # 52 週高低點（直接從已取得的歷史資料算，不另外呼叫 API）
        high52 = max(high_list) if high_list else None
        low52  = min(low_list)  if low_list  else None

        return jsonify({
            "code": code, "name": realtime["name"], "market": "tw",
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
            "sector": None, "currency": "TWD", "updated_at": datetime.now().isoformat()
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route("/api/screen")
def screen_stocks():
    codes_param = request.args.get("codes", "")
    if not codes_param:
        return jsonify({"error": "請提供股票代號列表"}), 400
    codes = [c.strip().replace(".TW", "").upper() for c in codes_param.split(",") if c.strip()]
    if len(codes) > 30:
        return jsonify({"error": "單次最多查詢 30 檔"}), 400

    results, errors = [], []
    for code in codes:
        try:
            realtime = get_realtime(code)
            close_list, high_list, low_list = get_history(code)
            if not close_list:
                errors.append(code); continue
            if not realtime:
                price  = close_list[-1]
                prev   = close_list[-2] if len(close_list) >= 2 else price
                realtime = {"code": code, "name": code, "price": price,
                            "change": round((price-prev)/prev*100,2) if prev else None}
            time.sleep(0.3)
            pe_data   = get_pe_yield(code)
            pe        = pe_data.get("pe")
            yield_pct = pe_data.get("yield_pct")
            rsi      = calc_rsi(close_list) if len(close_list) >= 15 else None
            macd_sig = calc_macd(close_list) if len(close_list) >= 26 else "unknown"
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

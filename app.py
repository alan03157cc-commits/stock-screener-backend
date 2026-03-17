"""
選股雷達 — Python Flask 後端 v5
台股資料來源：twstock 套件（支援上市+上櫃）
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
import twstock
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import traceback
import time
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)
CORS(app)

# ===== 工具函式 =====

def safe_val(val, decimals=2):
    if val is None:
        return None
    try:
        f = float(val)
        return None if (np.isnan(f) or np.isinf(f)) else round(f, decimals)
    except Exception:
        return None

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

def calc_kd(h, l, c, period=9):
    if len(c) < period:
        return None, None
    lmin = pd.Series(l).rolling(period).min()
    hmax = pd.Series(h).rolling(period).max()
    diff = (hmax - lmin).replace(0, np.nan)
    rsv  = (pd.Series(c) - lmin) / diff * 100
    k = rsv.ewm(com=2, adjust=False).mean()
    d = k.ewm(com=2, adjust=False).mean()
    return safe_val(k.iloc[-1], 1), safe_val(d.iloc[-1], 1)

def get_ma_state(prices):
    if len(prices) < 20:
        return "unknown"
    s = pd.Series(prices)
    cur  = s.iloc[-1]
    ma5  = s.rolling(5).mean().iloc[-1]
    ma20 = s.rolling(20).mean().iloc[-1]
    if len(prices) >= 60:
        ma60 = s.rolling(60).mean().iloc[-1]
        if cur > ma5 > ma20 > ma60:
            return "all_above"
    if ma5 > ma20 and cur > ma20:
        return "golden_cross"
    if cur > ma20:
        return "above_ma20"
    return "below_all"

def gen_signals(pe, yield_pct, rsi, macd, ma_state):
    s = []
    if macd == "bullish":              s.append("MACD黃金交叉")
    if ma_state == "all_above":        s.append("多頭排列")
    elif ma_state == "golden_cross":   s.append("均線交叉")
    if rsi and rsi < 30:               s.append("RSI超賣")
    if yield_pct and yield_pct >= 4:   s.append("高殖利率")
    if pe and pe < 12:                 s.append("低本益比")
    if rsi and 50 < rsi < 70:          s.append("強勢區間")
    return s[:4]

# ===== 取得股票資料（twstock）=====

def fetch_stock(code):
    code = str(code).strip().upper().replace(".TW", "").replace(".TWO", "")

    # 確認股票是否存在（上市或上櫃）
    stock_info = twstock.codes.get(code)
    if not stock_info:
        return None, f"找不到股票：{code}"

    stock_name = stock_info.name
    market_type = "tse" if stock_info.market == "上市" else "otc"

    # 取得近 6 個月歷史資料
    stock = twstock.Stock(code)
    now = datetime.now()
    start_year  = now.year
    start_month = now.month - 5
    if start_month <= 0:
        start_month += 12
        start_year -= 1
    stock.fetch_from(start_year, start_month)

    if not stock.price or len(stock.price) < 5:
        return None, f"無法取得 {code} 的歷史資料"

    close_list = [safe_val(p) for p in stock.price if p]
    high_list  = [safe_val(p) for p in stock.high  if p]
    low_list   = [safe_val(p) for p in stock.low   if p]

    price    = close_list[-1]
    prev     = close_list[-2] if len(close_list) >= 2 else price
    change   = round((price - prev) / prev * 100, 2) if prev and prev != 0 else None

    # 即時報價（交易時間內）
    try:
        rt = twstock.realtime.get(code)
        if rt and rt.get("success") and rt["realtime"].get("latest_trade_price"):
            rt_price = safe_val(rt["realtime"]["latest_trade_price"])
            if rt_price and rt_price > 0:
                price  = rt_price
                change = round((price - prev) / prev * 100, 2) if prev else change
    except Exception:
        pass

    # 技術指標
    rsi      = calc_rsi(close_list) if len(close_list) >= 15 else None
    macd_sig = calc_macd(close_list) if len(close_list) >= 26 else "unknown"
    k_val, d_val = calc_kd(high_list, low_list, close_list)
    ma_state = get_ma_state(close_list)
    high52   = max(high_list) if high_list else None
    low52    = min(low_list)  if low_list  else None
    recent30 = close_list[-30:]

    # 本益比殖利率（twstock 目前不直接提供，用 TWSE/TPEx API 補）
    pe, yield_pct, pb = fetch_pe_yield(code, market_type)

    return {
        "code": code, "name": stock_name,
        "market": "tw", "market_type": market_type,
        "price": price, "prev_close": prev, "change": change,
        "day_high": high_list[-1] if high_list else None,
        "day_low":  low_list[-1]  if low_list  else None,
        "week_52_high": high52, "week_52_low": low52,
        "pe": pe, "pb": pb, "yield_pct": yield_pct,
        "roe": None, "eps_growth": None, "gross": None, "debt": None, "cap": "large",
        "rsi": rsi, "macd": macd_sig, "kd": k_val, "kd_d": d_val, "ma_state": ma_state,
        "foreign_days": None, "invest": None, "margin_pct": None,
        "price_history": recent30,
        "signals": gen_signals(pe, yield_pct, rsi, macd_sig, ma_state),
        "sector": stock_info.group if stock_info else None,
        "currency": "TWD", "updated_at": datetime.now().isoformat()
    }, None


def fetch_pe_yield(code, market_type):
    """補充抓取本益比與殖利率"""
    import requests
    HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://mis.twse.com.tw/"}
    today = datetime.now()

    if market_type == "tse":
        url = "https://www.twse.com.tw/exchangeReport/BWIBBU_d"
        for i in range(5):
            d = (today - timedelta(days=30 * i)).strftime("%Y%m01")
            try:
                res = requests.get(url, params={"response": "json", "date": d, "stockNo": code},
                                   headers=HEADERS, timeout=8, verify=False)
                data = res.json()
                if data.get("stat") == "OK":
                    for row in reversed(data.get("data") or []):
                        pe = safe_val(row[3]); yld = safe_val(row[1]); pb = safe_val(row[5])
                        if pe or yld:
                            return pe, yld, pb
                time.sleep(0.3)
            except Exception:
                pass
    else:
        url = "https://www.tpex.org.tw/web/stock/aftertrading/peratio_analysis/pera_result.php"
        for i in range(5):
            target = today - timedelta(days=30 * i)
            roc = target.year - 1911
            d = f"{roc}/{target.month:02d}"
            try:
                res = requests.get(url, params={"d": d, "stkno": code, "l": "zh-tw", "o": "json"},
                                   headers={**HEADERS, "Referer": "https://www.tpex.org.tw/"},
                                   timeout=8, verify=False)
                rows = res.json().get("aaData") or []
                for row in reversed(rows):
                    pe = safe_val(row[1]); yld = safe_val(row[3]); pb = safe_val(row[4])
                    if pe or yld:
                        return pe, yld, pb
                time.sleep(0.3)
            except Exception:
                pass
    return None, None, None


# ===== API 端點 =====

@app.route("/")
def index():
    return jsonify({"status": "ok", "message": "選股雷達後端 v5（twstock 引擎）",
                    "time": datetime.now().isoformat()})

@app.route("/api/stock/<code>")
def get_stock(code):
    try:
        data, err = fetch_stock(code)
        if err:
            return jsonify({"error": err}), 404
        return jsonify(data)
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
            data, err = fetch_stock(code)
            if err or not data:
                errors.append(code)
                continue
            results.append({
                "code": data["code"], "name": data["name"], "market": "tw",
                "price": data["price"], "change": data["change"],
                "pe": data["pe"], "yield_pct": data["yield_pct"],
                "roe": None, "gross": None, "debt": None,
                "rsi": data["rsi"], "macd": data["macd"],
                "kd": data["kd"], "ma_state": data["ma_state"],
                "cap": "large", "foreign_days": None, "invest": None, "margin_pct": None,
                "signals": data["signals"],
            })
            time.sleep(0.8)
        except Exception as e:
            errors.append(code)
            print(f"[ERROR] {code}: {e}")

    return jsonify({"results": results, "total": len(results),
                    "errors": errors, "updated_at": datetime.now().isoformat()})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)

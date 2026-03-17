from flask import Flask, jsonify, request
from flask_cors import CORS
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
import traceback
import requests  # 引入 requests 準備做偽裝

app = Flask(__name__)
CORS(app)

@app.route('/')
def home():
    return "選股雷達 🚀 (yfinance 引擎運作中：支援上市/上櫃/美股，已開啟防擋機制)"

# ===== 技術指標計算區 =====
def calc_rsi(prices, period=14):
    if len(prices) < period + 1: return None
    deltas = np.diff(prices)
    seed = deltas[:period]
    up = seed[seed >= 0].sum() / period
    down = -seed[seed < 0].sum() / period
    rs = up / down if down != 0 else 0
    rsi = np.zeros_like(prices)
    rsi[:period] = 100. - 100. / (1. + rs)
    for i in range(period, len(prices)):
        delta = deltas[i - 1]
        upval = delta if delta > 0 else 0.
        downval = -delta if delta < 0 else 0.
        up = (up * (period - 1) + upval) / period
        down = (down * (period - 1) + downval) / period
        rs = up / down if down != 0 else 0
        rsi[i] = 100. - 100. / (1. + rs)
    return round(rsi[-1], 2)

def calc_macd(prices):
    if len(prices) < 26: return "unknown"
    s = pd.Series(prices)
    ema12 = s.ewm(span=12, adjust=False).mean()
    ema26 = s.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    hist = macd - signal
    if macd.iloc[-1] > signal.iloc[-1] and macd.iloc[-2] <= signal.iloc[-2]: return "bullish"
    if macd.iloc[-1] < signal.iloc[-1] and macd.iloc[-2] >= signal.iloc[-2]: return "bearish"
    return "positive" if hist.iloc[-1] > 0 else "negative"

def get_ma_state(prices):
    if len(prices) < 60: return "unknown"
    s = pd.Series(prices)
    ma5 = s.rolling(5).mean().iloc[-1]
    ma20 = s.rolling(20).mean().iloc[-1]
    ma60 = s.rolling(60).mean().iloc[-1]
    current = prices[-1]
    if current > ma5 > ma20 > ma60: return "all_above"
    if current > ma20: return "above_ma20"
    return "neutral"

def calc_kd(highs, lows, closes, n=9):
    if len(closes) < n: return None, None
    h = pd.Series(highs)
    l = pd.Series(lows)
    c = pd.Series(closes)
    rsv = (c - l.rolling(n).min()) / (h.rolling(n).max() - l.rolling(n).min()) * 100
    rsv = rsv.fillna(50)
    k = rsv.ewm(com=2, adjust=False).mean()
    d = k.ewm(com=2, adjust=False).mean()
    return round(k.iloc[-1], 2), round(d.iloc[-1], 2)

def gen_signals(pe, yield_pct, rsi, macd, ma_state):
    sigs = []
    if pe and pe < 15: sigs.append("低本益比")
    if yield_pct and yield_pct > 5: sigs.append("高配息")
    if rsi and rsi < 30: sigs.append("超賣區")
    if macd == "bullish": sigs.append("黃金交叉")
    if ma_state == "all_above": sigs.append("多頭排列")
    return sigs

# ===== 核心抓取資料邏輯 =====
def fetch_stock_data(code):
    code = str(code).upper().strip()
    is_tw = code.isdigit() # 全數字代表是台股
    
    # 🌟 建立一個「偽裝成真人瀏覽器」的會話 (Session)，突破 Yahoo 防線
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    })
    
    ticker_name = code
    if is_tw:
        # 台股自動判斷：先試上市 (.TW)，帶入偽裝 session
        t = yf.Ticker(f"{code}.TW", session=session)
        if t.history(period="1d").empty:
            ticker_name = f"{code}.TWO"
            t = yf.Ticker(ticker_name, session=session)
        else:
            ticker_name = f"{code}.TW"
    else:
        # 美股直接抓，帶入偽裝 session
        t = yf.Ticker(code, session=session)
        
    hist = t.history(period="6mo")
    if hist.empty:
        raise Exception(f"找不到股票代號或無交易資料: {code}")
        
    info = t.info
    closes = hist['Close'].tolist()
    highs = hist['High'].tolist()
    lows = hist['Low'].tolist()
    
    price = round(closes[-1], 2)
    prev = round(closes[-2], 2) if len(closes) > 1 else price
    change_pct = round((price - prev) / prev * 100, 2) if prev else 0
    
    pe = info.get('trailingPE')
    yield_pct = info.get('dividendYield')
    roe = info.get('returnOnEquity')
    eps_growth = info.get('earningsGrowth')
    gross = info.get('grossMargins')
    debt = info.get('debtToEquity')
    
    # 處理 None 值與百分比轉換
    pe = round(pe, 2) if pe else None
    yield_pct = round(yield_pct * 100, 2) if yield_pct else None
    roe = round(roe * 100, 2) if roe else None
    eps_growth = round(eps_growth * 100, 2) if eps_growth else None
    gross = round(gross * 100, 2) if gross else None
    debt = round(debt, 2) if debt else None

    rsi = calc_rsi(closes)
    macd = calc_macd(closes)
    ma_state = get_ma_state(closes)
    k, d = calc_kd(highs, lows, closes)
    
    market_flag = "tw" if is_tw else "us"

    return {
        "code": code,
        "name": info.get('shortName', code),
        "market": market_flag,
        "price": price,
        "change": change_pct,
        "pe": pe,
        "roe": roe,
        "yield_pct": yield_pct,
        "eps_growth": eps_growth,
        "gross": gross,
        "debt": debt,
        "sector": info.get('sector', 'Unknown'),
        "rsi": rsi,
        "macd": macd,
        "kd": k,
        "kd_d": d,
        "ma_state": ma_state,
        "week_52_high": info.get('fiftyTwoWeekHigh'),
        "week_52_low": info.get('fiftyTwoWeekLow'),
        "price_history": [round(x, 2) for x in closes[-30:]], # 取最近30天畫線
        "signals": gen_signals(pe, yield_pct, rsi, macd, ma_state),
        "updated_at": datetime.now().isoformat()
    }

# ===== API 路由 =====
@app.route("/api/stock/<code>")
def get_stock(code):
    try:
        data = fetch_stock_data(code)
        return jsonify(data)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 404

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)

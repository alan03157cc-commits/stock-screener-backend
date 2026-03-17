"""
選股雷達 — Python Flask 後端 Proxy
功能：向 Yahoo Finance 取得股票資料，並回傳給前端
部署目標：Render 免費雲端平台
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import traceback

app = Flask(__name__)

# 允許所有來源跨域存取（部署後可改為指定網域）
CORS(app)

# ===== 工具函式 =====

def safe_val(val, decimals=2):
    """安全轉換數值，避免 NaN / None 出錯"""
    if val is None:
        return None
    try:
        f = float(val)
        if np.isnan(f) or np.isinf(f):
            return None
        return round(f, decimals)
    except Exception:
        return None


def calc_rsi(prices, period=14):
    """計算 RSI 指標"""
    delta = prices.diff()
    gain = delta.clip(lower=0).rolling(window=period).mean()
    loss = -delta.clip(upper=0).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return safe_val(rsi.iloc[-1], 1)


def calc_macd(prices):
    """計算 MACD 指標，回傳訊號字串"""
    ema12 = prices.ewm(span=12, adjust=False).mean()
    ema26 = prices.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    histogram = macd_line - signal_line

    macd_now = macd_line.iloc[-1]
    signal_now = signal_line.iloc[-1]
    macd_prev = macd_line.iloc[-2]
    signal_prev = signal_line.iloc[-2]
    hist_now = histogram.iloc[-1]

    # 黃金交叉：MACD 由下往上穿越 Signal
    if macd_prev < signal_prev and macd_now > signal_now:
        return "bullish"
    # 死亡交叉：MACD 由上往下穿越 Signal
    elif macd_prev > signal_prev and macd_now < signal_now:
        return "bearish"
    # 柱狀圖為正
    elif hist_now > 0:
        return "positive"
    else:
        return "negative"


def calc_kd(high, low, close, period=9):
    """計算 KD 隨機指標"""
    low_min = low.rolling(window=period).min()
    high_max = high.rolling(window=period).max()
    rsv = (close - low_min) / (high_max - low_min) * 100
    k = rsv.ewm(com=2, adjust=False).mean()
    d = k.ewm(com=2, adjust=False).mean()
    return safe_val(k.iloc[-1], 1), safe_val(d.iloc[-1], 1)


def get_ma_state(prices):
    """判斷均線狀態"""
    if len(prices) < 60:
        return "unknown"

    ma5 = prices.rolling(5).mean().iloc[-1]
    ma20 = prices.rolling(20).mean().iloc[-1]
    ma60 = prices.rolling(60).mean().iloc[-1]
    current = prices.iloc[-1]

    if current > ma5 > ma20 > ma60:
        return "all_above"        # 多頭排列
    elif ma5 > ma20:
        return "golden_cross"     # 黃金交叉
    elif current > ma20:
        return "above_ma20"       # 站上月線
    elif current > ma60:
        return "above_ma60"       # 站上季線
    else:
        return "below_all"        # 弱勢


def get_tw_ticker(code):
    """台股代號轉換為 Yahoo Finance 格式"""
    code = code.strip().upper()
    # 台灣股票加上 .TW 後綴
    if not code.endswith(".TW") and not code.endswith(".TWO"):
        # 上市股票加 .TW，但若查不到可嘗試 .TWO（上櫃）
        return code + ".TW"
    return code


# ===== API 端點 =====

@app.route("/")
def index():
    """健康檢查端點"""
    return jsonify({
        "status": "ok",
        "message": "選股雷達後端運作中",
        "time": datetime.now().isoformat()
    })


@app.route("/api/stock/<code>")
def get_stock(code):
    """
    查詢單一股票完整資料
    參數：
        code  — 股票代號 (如 2330、AAPL)
        market — 市場 tw / us（query string，預設 tw）
    """
    market = request.args.get("market", "tw").lower()

    # 轉換台股代號格式
    ticker_code = get_tw_ticker(code) if market == "tw" else code.upper()

    try:
        ticker = yf.Ticker(ticker_code)
        info = ticker.info

        # 若找不到股票
        if not info or info.get("regularMarketPrice") is None:
            # 台股嘗試 .TWO（上櫃板）
            if market == "tw" and not ticker_code.endswith(".TWO"):
                ticker_code2 = code.strip().upper() + ".TWO"
                ticker = yf.Ticker(ticker_code2)
                info = ticker.info
                if not info or info.get("regularMarketPrice") is None:
                    return jsonify({"error": f"找不到股票：{code}"}), 404
                ticker_code = ticker_code2
            else:
                return jsonify({"error": f"找不到股票：{code}"}), 404

        # 下載近 3 個月歷史價格（用於技術指標計算）
        hist = ticker.history(period="3mo")

        if hist.empty:
            return jsonify({"error": "無法取得歷史資料"}), 404

        close = hist["Close"]
        high = hist["High"]
        low = hist["Low"]
        volume = hist["Volume"]

        # ----- 基本資訊 -----
        current_price = safe_val(info.get("regularMarketPrice") or close.iloc[-1])
        prev_close = safe_val(info.get("regularMarketPreviousClose") or close.iloc[-2])
        change_pct = safe_val(
            (current_price - prev_close) / prev_close * 100
            if current_price and prev_close and prev_close != 0 else None, 2
        )

        # ----- 基本面 -----
        pe = safe_val(info.get("trailingPE") or info.get("forwardPE"))
        eps = safe_val(info.get("trailingEps"))

        # EPS 成長率（用 earnings_growth 或自行計算）
        eps_growth = safe_val(
            (info.get("earningsGrowth") or 0) * 100, 1
        ) if info.get("earningsGrowth") else None

        roe = safe_val(
            (info.get("returnOnEquity") or 0) * 100, 1
        ) if info.get("returnOnEquity") else None

        # 殖利率
        dividend_yield = safe_val(
            (info.get("dividendYield") or 0) * 100, 2
        ) if info.get("dividendYield") else 0.0

        # 毛利率
        gross_margin = safe_val(
            (info.get("grossMargins") or 0) * 100, 1
        ) if info.get("grossMargins") else None

        # 負債比率
        total_debt = info.get("totalDebt") or 0
        total_assets = info.get("totalAssets") or 1
        debt_ratio = safe_val(total_debt / total_assets * 100, 1) if total_assets else None

        # 市值分類
        market_cap = info.get("marketCap") or 0
        if market_cap > 1e12:      # > 1 兆（大型）
            cap_category = "large"
        elif market_cap > 1e11:    # > 1000 億（中型）
            cap_category = "mid"
        else:
            cap_category = "small"

        # ----- 技術指標 -----
        rsi = calc_rsi(close)
        macd_signal = calc_macd(close)
        k_val, d_val = calc_kd(high, low, close)
        ma_state = get_ma_state(close)

        # 近 30 天收盤價（用於前端走勢圖）
        recent_30 = close.tail(30).tolist()
        recent_30 = [safe_val(p) for p in recent_30]

        # ----- 組合結果 -----
        result = {
            "code": code.upper(),
            "ticker": ticker_code,
            "name": info.get("longName") or info.get("shortName") or code,
            "market": market,

            # 價格
            "price": current_price,
            "prev_close": prev_close,
            "change": change_pct,
            "day_high": safe_val(info.get("dayHigh")),
            "day_low": safe_val(info.get("dayLow")),
            "week_52_high": safe_val(info.get("fiftyTwoWeekHigh")),
            "week_52_low": safe_val(info.get("fiftyTwoWeekLow")),

            # 基本面
            "pe": pe,
            "eps": eps,
            "eps_growth": eps_growth,
            "roe": roe,
            "yield_pct": dividend_yield,
            "gross": gross_margin,
            "debt": debt_ratio,
            "market_cap": market_cap,
            "cap": cap_category,

            # 技術面
            "rsi": rsi,
            "macd": macd_signal,
            "kd": k_val,
            "kd_d": d_val,
            "ma_state": ma_state,

            # 籌碼面（Yahoo Finance 不提供台股籌碼，以 None 回傳）
            "foreign_days": None,
            "invest": None,
            "margin_pct": None,

            # 走勢圖資料
            "price_history": recent_30,

            # 其他資訊
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "currency": info.get("currency", "TWD" if market == "tw" else "USD"),
            "updated_at": datetime.now().isoformat()
        }

        return jsonify(result)

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/screen")
def screen_stocks():
    """
    批次篩選股票
    接受 JSON body 的篩選條件，回傳符合的股票列表
    注意：批次查詢較慢，建議前端顯示進度條
    """
    # 從 query string 取得條件
    codes_param = request.args.get("codes", "")
    market = request.args.get("market", "tw").lower()

    if not codes_param:
        return jsonify({"error": "請提供股票代號列表，用逗號分隔"}), 400

    codes = [c.strip() for c in codes_param.split(",") if c.strip()]

    if len(codes) > 50:
        return jsonify({"error": "單次最多查詢 50 檔"}), 400

    results = []
    errors = []

    for code in codes:
        try:
            ticker_code = get_tw_ticker(code) if market == "tw" else code.upper()
            ticker = yf.Ticker(ticker_code)
            info = ticker.info
            hist = ticker.history(period="3mo")

            if hist.empty or not info.get("regularMarketPrice"):
                errors.append(code)
                continue

            close = hist["Close"]
            high = hist["High"]
            low = hist["Low"]

            current_price = safe_val(info.get("regularMarketPrice") or close.iloc[-1])
            prev_close = safe_val(info.get("regularMarketPreviousClose") or close.iloc[-2])
            change_pct = safe_val(
                (current_price - prev_close) / prev_close * 100
                if current_price and prev_close and prev_close != 0 else 0, 2
            )

            pe = safe_val(info.get("trailingPE") or info.get("forwardPE"))
            roe = safe_val((info.get("returnOnEquity") or 0) * 100, 1) if info.get("returnOnEquity") else None
            dividend_yield = safe_val((info.get("dividendYield") or 0) * 100, 2) if info.get("dividendYield") else 0.0
            gross_margin = safe_val((info.get("grossMargins") or 0) * 100, 1) if info.get("grossMargins") else None
            total_debt = info.get("totalDebt") or 0
            total_assets = info.get("totalAssets") or 1
            debt_ratio = safe_val(total_debt / total_assets * 100, 1) if total_assets else None
            eps_growth = safe_val((info.get("earningsGrowth") or 0) * 100, 1) if info.get("earningsGrowth") else None

            rsi = calc_rsi(close)
            macd_signal = calc_macd(close)
            k_val, _ = calc_kd(high, low, close)
            ma_state = get_ma_state(close)

            market_cap = info.get("marketCap") or 0
            cap_category = "large" if market_cap > 1e12 else "mid" if market_cap > 1e11 else "small"

            results.append({
                "code": code.upper(),
                "name": info.get("shortName") or code,
                "market": market,
                "price": current_price,
                "change": change_pct,
                "pe": pe,
                "eps_growth": eps_growth,
                "roe": roe,
                "yield_pct": dividend_yield,
                "gross": gross_margin,
                "debt": debt_ratio,
                "rsi": rsi,
                "macd": macd_signal,
                "kd": k_val,
                "ma_state": ma_state,
                "cap": cap_category,
                "foreign_days": None,
                "invest": None,
                "margin_pct": None,
                "signals": _generate_signals(pe, roe, dividend_yield, rsi, macd_signal, ma_state, eps_growth),
            })

        except Exception as e:
            errors.append(code)
            print(f"[ERROR] {code}: {e}")

    return jsonify({
        "results": results,
        "total": len(results),
        "errors": errors,
        "updated_at": datetime.now().isoformat()
    })


def _generate_signals(pe, roe, yield_pct, rsi, macd, ma_state, eps_growth):
    """根據指標自動產生訊號標籤"""
    signals = []
    if macd == "bullish":
        signals.append("MACD黃金交叉")
    if ma_state == "all_above":
        signals.append("多頭排列")
    elif ma_state == "golden_cross":
        signals.append("黃金交叉")
    if rsi and rsi < 30:
        signals.append("RSI超賣")
    if yield_pct and yield_pct >= 4:
        signals.append("高殖利率")
    if roe and roe >= 20:
        signals.append("高ROE")
    if pe and pe < 12:
        signals.append("低本益比")
    if eps_growth and eps_growth >= 20:
        signals.append("EPS高成長")
    return signals[:4]  # 最多回傳 4 個標籤


# ===== 啟動伺服器 =====
if __name__ == "__main__":
    # 本機開發模式
    app.run(host="0.0.0.0", port=5000, debug=True)

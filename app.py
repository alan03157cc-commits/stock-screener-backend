"""
選股雷達 — Python Flask 後端
台股資料來源：台灣證券交易所 TWSE 官方 API（免費、無限制）
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import traceback
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
import time

# 關閉 SSL 警告（TWSE 憑證在某些環境下無法驗證）

app = Flask(__name__)
CORS(app)

# ===== TWSE API 端點 =====
TWSE_BASE = "https://www.twse.com.tw/rwd/zh"
TPEX_BASE = "https://www.tpex.org.tw/web/stock"  # 上櫃（OTC）

# ===== 工具函式 =====

def safe_val(val, decimals=2):
    """安全轉換數值，避免 NaN / None 出錯"""
    if val is None:
        return None
    try:
        f = float(str(val).replace(",", "").replace("+", "").replace("%", ""))
        if np.isnan(f) or np.isinf(f):
            return None
        return round(f, decimals)
    except Exception:
        return None


def get_twse_realtime(code):
    """
    取得台股報價
    交易時間：使用即時報價 API
    非交易時間：使用歷史資料 API 取最新收盤價
    """
    headers = {"User-Agent": "Mozilla/5.0"}

    # 先嘗試即時報價
    try:
        url = f"{TWSE_BASE}/stock/realTimeQuotes/list"
        res = requests.get(url, params={"stockNo": code, "response": "json"}, headers=headers, timeout=10, verify=False)
        data = res.json()
        if data.get("stat") == "OK" and data.get("data"):
            row = data["data"][0]
            price = safe_val(row[2])
            if price and price > 0:
                return {
                    "code": row[0].strip(),
                    "name": row[1].strip(),
                    "price": price,
                    "change": safe_val(row[3]),
                    "open": safe_val(row[4]),
                    "high": safe_val(row[5]),
                    "low": safe_val(row[6]),
                    "prev_close": safe_val(row[7]),
                    "volume": safe_val(row[8], 0),
                }
    except Exception as e:
        print(f"[TWSE realtime error] {code}: {e}")

    # 即時無資料（收盤後）→ 改用歷史 API 取最新收盤
    try:
        today = datetime.now()
        # 嘗試近兩個月，確保能取到資料
        for i in range(2):
            target = today - timedelta(days=30 * i)
            yyyymm = target.strftime("%Y%m")
            url2 = f"{TWSE_BASE}/stock/historicalDailyQuotes/list"
            res2 = requests.get(url2, params={"stockNo": code, "date": yyyymm + "01", "response": "json"}, headers=headers, timeout=10, verify=False)
            data2 = res2.json()
            if data2.get("stat") == "OK" and data2.get("data"):
                rows = data2["data"]
                # 取最後一筆（最新交易日）
                last = rows[-1]
                close = safe_val(last[6])
                prev_close = safe_val(rows[-2][6]) if len(rows) >= 2 else None
                name = data2.get("title", "").replace("臺灣證券交易所 股票每日收盤行情", "").strip()
                # title 格式：「...  股票代號 股票名稱」
                # 嘗試從 title 取名稱
                title_parts = data2.get("title", "").split()
                stock_name = title_parts[-1] if title_parts else code

                if close:
                    change_pct = round((close - prev_close) / prev_close * 100, 2) if prev_close and prev_close != 0 else None
                    return {
                        "code": code,
                        "name": stock_name,
                        "price": close,
                        "change": change_pct,
                        "open": safe_val(last[3]),
                        "high": safe_val(last[4]),
                        "low": safe_val(last[5]),
                        "prev_close": prev_close,
                        "volume": safe_val(last[1], 0),
                    }
            time.sleep(0.3)
    except Exception as e:
        print(f"[TWSE history fallback error] {code}: {e}")

    return None


def get_twse_history(code, days=90):
    """
    取得台股近期歷史收盤價（用於計算技術指標）
    使用 TWSE 月份歷史資料 API，取近 3 個月
    """
    all_prices = []
    all_high = []
    all_low = []
    all_close = []
    
    today = datetime.now()
    
    # 取近 3 個月資料
    for i in range(3):
        target = today - timedelta(days=30 * i)
        yyyymm = target.strftime("%Y%m")
        
        url = f"{TWSE_BASE}/stock/historicalDailyQuotes/list"
        params = {
            "stockNo": code,
            "date": yyyymm + "01",
            "response": "json"
        }
        headers = {"User-Agent": "Mozilla/5.0"}
        
        try:
            res = requests.get(url, params=params, headers=headers, timeout=10, verify=False)
            data = res.json()
            
            if data.get("stat") == "OK" and data.get("data"):
                for row in data["data"]:
                    try:
                        # TWSE 歷史欄位：日期、成交股數、成交金額、開盤、最高、最低、收盤、漲跌、成交筆數
                        close = safe_val(row[6])
                        high = safe_val(row[4])
                        low = safe_val(row[5])
                        if close:
                            all_close.append(close)
                            all_high.append(high or close)
                            all_low.append(low or close)
                    except:
                        continue
            time.sleep(0.3)  # 避免請求過快
        except Exception as e:
            print(f"[TWSE history error] {code} {yyyymm}: {e}")
    
    # 資料由舊到新排列
    all_close.reverse()
    all_high.reverse()
    all_low.reverse()
    
    return all_close, all_high, all_low


def get_twse_pe_roe(code):
    """
    取得台股本益比、殖利率、股價淨值比
    使用 TWSE 本益比統計 API
    """
    url = f"{TWSE_BASE}/stock/dividendYield/list"
    params = {"stockNo": code, "response": "json"}
    headers = {"User-Agent": "Mozilla/5.0"}
    
    try:
        res = requests.get(url, params=params, headers=headers, timeout=10, verify=False)
        data = res.json()
        if data.get("stat") == "OK" and data.get("data"):
            row = data["data"][-1]  # 最新一筆
            # 欄位：日期、殖利率、股利、本益比、財報年度、股價淨值比
            return {
                "yield_pct": safe_val(row[1]),
                "pe": safe_val(row[3]),
                "pb": safe_val(row[5]),
            }
    except Exception as e:
        print(f"[TWSE pe/roe error] {code}: {e}")
    return {}


def calc_rsi(prices, period=14):
    """計算 RSI 指標"""
    if len(prices) < period + 1:
        return None
    s = pd.Series(prices)
    delta = s.diff()
    gain = delta.clip(lower=0).rolling(window=period).mean()
    loss = -delta.clip(upper=0).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return safe_val(rsi.iloc[-1], 1)


def calc_macd(prices):
    """計算 MACD 訊號"""
    if len(prices) < 26:
        return "unknown"
    s = pd.Series(prices)
    ema12 = s.ewm(span=12, adjust=False).mean()
    ema26 = s.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    histogram = macd_line - signal_line

    if len(macd_line) < 2:
        return "unknown"

    macd_now = macd_line.iloc[-1]
    signal_now = signal_line.iloc[-1]
    macd_prev = macd_line.iloc[-2]
    signal_prev = signal_line.iloc[-2]

    if macd_prev < signal_prev and macd_now > signal_now:
        return "bullish"
    elif macd_prev > signal_prev and macd_now < signal_now:
        return "bearish"
    elif histogram.iloc[-1] > 0:
        return "positive"
    else:
        return "negative"


def calc_kd(high_list, low_list, close_list, period=9):
    """計算 KD 隨機指標"""
    if len(close_list) < period:
        return None, None
    low_min = pd.Series(low_list).rolling(window=period).min()
    high_max = pd.Series(high_list).rolling(window=period).max()
    diff = high_max - low_min
    diff = diff.replace(0, np.nan)
    rsv = (pd.Series(close_list) - low_min) / diff * 100
    k = rsv.ewm(com=2, adjust=False).mean()
    d = k.ewm(com=2, adjust=False).mean()
    return safe_val(k.iloc[-1], 1), safe_val(d.iloc[-1], 1)


def get_ma_state(prices):
    """判斷均線狀態"""
    if len(prices) < 60:
        return "unknown"
    s = pd.Series(prices)
    ma5 = s.rolling(5).mean().iloc[-1]
    ma20 = s.rolling(20).mean().iloc[-1]
    ma60 = s.rolling(60).mean().iloc[-1]
    current = s.iloc[-1]

    if current > ma5 > ma20 > ma60:
        return "all_above"
    elif ma5 > ma20:
        return "golden_cross"
    elif current > ma20:
        return "above_ma20"
    elif current > ma60:
        return "above_ma60"
    else:
        return "below_all"


def generate_signals(pe, roe, yield_pct, rsi, macd, ma_state, eps_growth=None):
    """自動產生選股訊號標籤"""
    signals = []
    if macd == "bullish":
        signals.append("MACD黃金交叉")
    if ma_state == "all_above":
        signals.append("多頭排列")
    elif ma_state == "golden_cross":
        signals.append("均線黃金交叉")
    if rsi and rsi < 30:
        signals.append("RSI超賣")
    if yield_pct and yield_pct >= 4:
        signals.append("高殖利率")
    if pe and pe < 12:
        signals.append("低本益比")
    if rsi and 50 < rsi < 70:
        signals.append("強勢區間")
    return signals[:4]


# ===== API 端點 =====

@app.route("/")
def index():
    """健康檢查"""
    return jsonify({
        "status": "ok",
        "message": "選股雷達後端運作中",
        "source": "TWSE 官方 API",
        "time": datetime.now().isoformat()
    })


@app.route("/api/stock/<code>")
def get_stock(code):
    """
    查詢單一台股個股完整資料
    資料來源：台灣證券交易所 TWSE
    """
    code = code.strip().upper().replace(".TW", "")

    try:
        # 1. 取得即時報價
        realtime = get_twse_realtime(code)
        if not realtime:
            return jsonify({"error": f"找不到股票：{code}，請確認代號是否正確"}), 404

        price = realtime["price"]
        prev_close = realtime["prev_close"]

        # 計算漲跌幅
        change_pct = None
        if price and prev_close and prev_close != 0:
            change_pct = round((price - prev_close) / prev_close * 100, 2)

        # 2. 取得歷史價格（計算技術指標用）
        time.sleep(0.3)
        close_list, high_list, low_list = get_twse_history(code)

        # 3. 取得本益比、殖利率
        time.sleep(0.3)
        pe_data = get_twse_pe_roe(code)

        # 4. 計算技術指標
        rsi = calc_rsi(close_list) if len(close_list) >= 15 else None
        macd_signal = calc_macd(close_list) if len(close_list) >= 26 else "unknown"
        k_val, d_val = calc_kd(high_list, low_list, close_list)
        ma_state = get_ma_state(close_list)

        # 5. 近 30 天走勢
        recent_30 = close_list[-30:] if len(close_list) >= 30 else close_list

        # 6. 組合結果
        pe = pe_data.get("pe")
        yield_pct = pe_data.get("yield_pct")

        result = {
            "code": code,
            "name": realtime["name"],
            "market": "tw",

            # 價格
            "price": price,
            "prev_close": prev_close,
            "change": change_pct,
            "day_high": realtime.get("high"),
            "day_low": realtime.get("low"),
            "open": realtime.get("open"),
            "volume": realtime.get("volume"),

            # 基本面（來自 TWSE）
            "pe": pe,
            "pb": pe_data.get("pb"),
            "yield_pct": yield_pct,
            "roe": None,        # TWSE 免費 API 不提供 ROE，需財報 API
            "eps_growth": None,
            "gross": None,
            "debt": None,
            "cap": "large",     # 預設，可依市值另行分類

            # 技術面
            "rsi": rsi,
            "macd": macd_signal,
            "kd": k_val,
            "kd_d": d_val,
            "ma_state": ma_state,

            # 籌碼面（TWSE 免費 API 不提供）
            "foreign_days": None,
            "invest": None,
            "margin_pct": None,

            # 走勢圖
            "price_history": recent_30,

            # 訊號
            "signals": generate_signals(pe, None, yield_pct, rsi, macd_signal, ma_state),

            "currency": "TWD",
            "updated_at": datetime.now().isoformat()
        }

        return jsonify(result)

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/screen")
def screen_stocks():
    """
    批次篩選台股
    codes: 逗號分隔的股票代號
    """
    codes_param = request.args.get("codes", "")
    if not codes_param:
        return jsonify({"error": "請提供股票代號列表"}), 400

    codes = [c.strip().replace(".TW", "") for c in codes_param.split(",") if c.strip()]
    if len(codes) > 30:
        return jsonify({"error": "單次最多查詢 30 檔"}), 400

    results = []
    errors = []

    for code in codes:
        try:
            realtime = get_twse_realtime(code)
            if not realtime:
                errors.append(code)
                continue

            time.sleep(0.5)  # 避免請求過快
            close_list, high_list, low_list = get_twse_history(code)

            time.sleep(0.3)
            pe_data = get_twse_pe_roe(code)

            price = realtime["price"]
            prev_close = realtime["prev_close"]
            change_pct = round((price - prev_close) / prev_close * 100, 2) if price and prev_close and prev_close != 0 else None

            rsi = calc_rsi(close_list) if len(close_list) >= 15 else None
            macd_signal = calc_macd(close_list) if len(close_list) >= 26 else "unknown"
            k_val, _ = calc_kd(high_list, low_list, close_list)
            ma_state = get_ma_state(close_list)

            pe = pe_data.get("pe")
            yield_pct = pe_data.get("yield_pct")

            results.append({
                "code": code,
                "name": realtime["name"],
                "market": "tw",
                "price": price,
                "change": change_pct,
                "pe": pe,
                "yield_pct": yield_pct,
                "roe": None,
                "gross": None,
                "debt": None,
                "rsi": rsi,
                "macd": macd_signal,
                "kd": k_val,
                "ma_state": ma_state,
                "cap": "large",
                "foreign_days": None,
                "invest": None,
                "margin_pct": None,
                "signals": generate_signals(pe, None, yield_pct, rsi, macd_signal, ma_state),
            })

            time.sleep(0.5)

        except Exception as e:
            errors.append(code)
            print(f"[ERROR] {code}: {e}")

    return jsonify({
        "results": results,
        "total": len(results),
        "errors": errors,
        "updated_at": datetime.now().isoformat()
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)

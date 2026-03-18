"""
選股雷達 — Python Flask 後端 v11
資料來源整合：
  - 即時報價：TWSE MIS API
  - 歷史/技術面：FinMind（免費）
  - 財務面：Yahoo Finance（yfinance，單一查詢不會被擋）
  - 籌碼面：TWSE 三大法人公開 API
  - 本益比殖利率：TWSE/TPEx 官方
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import yfinance as yf
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
FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
MIS_URL     = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
TSE_PE      = "https://www.twse.com.tw/exchangeReport/BWIBBU_d"
TSE_CHIP    = "https://www.twse.com.tw/fund/T86"          # 三大法人買賣
TSE_MARGIN  = "https://www.twse.com.tw/exchangeReport/MI_MARGN"  # 融資融券

MIS_HDR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://mis.twse.com.tw/",
    "Accept": "application/json",
}
HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# ===== 記憶體快取 =====
_cache = {}  # key -> (timestamp, data)
CACHE_TTL = 3600  # 1 小時

def cache_get(key):
    if key in _cache:
        ts, data = _cache[key]
        if time.time() - ts < CACHE_TTL:
            return data
    return None

def cache_set(key, data):
    _cache[key] = (time.time(), data)

def safe(v, d=2):
    if v is None or str(v).strip() in ("", "-", "--", "N/A", "nan"):
        return None
    try:
        f = float(str(v).replace(",", "").replace("+", ""))
        return None if (np.isnan(f) or np.isinf(f)) else round(f, d)
    except Exception:
        return None

# ===== 即時報價（MIS）=====

def get_realtime(code):
    cached = cache_get(f"rt_{code}")
    if cached:
        return cached

    for prefix in ["tse", "otc"]:
        try:
            r = requests.get(MIS_URL,
                params={"ex_ch": f"{prefix}_{code}.tw", "json": 1, "delay": 0},
                headers=MIS_HDR, timeout=6, verify=False)
            msg = r.json().get("msgArray", [])
            if msg and msg[0].get("n"):
                s = msg[0]
                price = safe(s.get("z")) or safe(s.get("y"))
                prev  = safe(s.get("y"))
                chg   = round((price-prev)/prev*100,2) if price and prev and prev!=0 else None
                result = (prefix, {
                    "name": s.get("n", code), "price": price, "prev_close": prev,
                    "change": chg, "open": safe(s.get("o")),
                    "high": safe(s.get("h")), "low": safe(s.get("l")),
                    "volume": safe(s.get("v"), 0),
                })
                cache_set(f"rt_{code}", result)
                return result
        except Exception as e:
            print(f"[MIS {prefix}] {code}: {e}")
        time.sleep(0.1)
    return None, None

# ===== 歷史資料（FinMind）=====

def get_history(code, months=6):
    cached = cache_get(f"hist_{code}")
    if cached:
        return cached

    end   = datetime.now()
    start = end - timedelta(days=30*months)
    try:
        r = requests.get(FINMIND_URL,
            params={"dataset":"TaiwanStockPrice","data_id":code,
                    "start_date":start.strftime("%Y-%m-%d"),
                    "end_date":end.strftime("%Y-%m-%d")},
            headers=HDR, timeout=15)
        rows = r.json().get("data", [])
        if not rows:
            return [], [], [], []

        cl = [safe(x["close"]) for x in rows if safe(x.get("close"))]
        hl = [safe(x["max"])   for x in rows if safe(x.get("max"))]
        ll = [safe(x["min"])   for x in rows if safe(x.get("min"))]
        vl = [safe(x.get("Trading_Volume"), 0) for x in rows]

        result = cl, hl, ll, vl
        cache_set(f"hist_{code}", result)
        return result
    except Exception as e:
        print(f"[FinMind hist] {code}: {e}")
        return [], [], [], []

# ===== 財務面（Yahoo Finance 單一查詢）=====

def get_financials(code, mtype):
    cached = cache_get(f"fin_{code}")
    if cached:
        return cached

    try:
        ticker_str = f"{code}.TW" if mtype != "otc" else f"{code}.TWO"
        t = yf.Ticker(ticker_str)
        info = t.info

        roe        = safe((info.get("returnOnEquity") or 0) * 100, 1) if info.get("returnOnEquity") else None
        gross      = safe((info.get("grossMargins") or 0) * 100, 1)   if info.get("grossMargins")    else None
        net_margin = safe((info.get("profitMargins") or 0) * 100, 1)  if info.get("profitMargins")   else None
        debt_ratio = None
        if info.get("totalDebt") and info.get("totalAssets"):
            debt_ratio = safe(info["totalDebt"] / info["totalAssets"] * 100, 1)
        eps_growth = safe((info.get("earningsGrowth") or 0) * 100, 1) if info.get("earningsGrowth") else None
        revenue_growth = safe((info.get("revenueGrowth") or 0) * 100, 1) if info.get("revenueGrowth") else None
        sector     = info.get("sector") or info.get("industry")
        market_cap = info.get("marketCap")
        cap_cat    = "large" if market_cap and market_cap > 1e11 else "mid" if market_cap and market_cap > 1e10 else "small"

        result = {
            "roe": roe, "gross": gross, "net_margin": net_margin,
            "debt": debt_ratio, "eps_growth": eps_growth,
            "revenue_growth": revenue_growth, "sector": sector, "cap": cap_cat,
            "week_52_high": safe(info.get("fiftyTwoWeekHigh")),
            "week_52_low":  safe(info.get("fiftyTwoWeekLow")),
        }
        cache_set(f"fin_{code}", result)
        return result
    except Exception as e:
        print(f"[Yahoo fin] {code}: {e}")
        return {}

# ===== 本益比殖利率（TWSE）=====

def get_pe_yield(code, mtype, months=4):
    cached = cache_get(f"pe_{code}")
    if cached:
        return cached

    for i in range(months):
        d = (datetime.now()-timedelta(days=30*i)).strftime("%Y%m01")
        try:
            r = requests.get(TSE_PE,
                params={"response":"json","date":d,"stockNo":code},
                headers=MIS_HDR, timeout=8, verify=False)
            data = r.json()
            if data.get("stat") == "OK":
                for row in reversed(data.get("data") or []):
                    pe=safe(row[3]); yld=safe(row[1]); pb=safe(row[5])
                    if pe or yld:
                        result = {"pe": pe, "yield_pct": yld, "pb": pb}
                        cache_set(f"pe_{code}", result)
                        return result
            time.sleep(0.2)
        except Exception as e:
            print(f"[TSE PE] {code}: {e}")
    return {}

# ===== 籌碼面（TWSE 三大法人）=====

def get_chip(code, mtype):
    cached = cache_get(f"chip_{code}")
    if cached:
        return cached

    try:
        today = datetime.now()
        # 找最近的交易日
        for i in range(5):
            d = (today - timedelta(days=i)).strftime("%Y%m%d")
            r = requests.get(TSE_CHIP,
                params={"response":"json","date":d,"selectType":"ALLBUT0999"},
                headers=MIS_HDR, timeout=8, verify=False)
            data = r.json()
            if data.get("stat") == "OK" and data.get("data"):
                for row in data["data"]:
                    if str(row[0]).strip() == code:
                        # 欄位：代號、名稱、外資買、外資賣、外資淨、投信買、投信賣、投信淨、自營買、自營賣、自營淨、三大法人合計
                        foreign_net = safe(row[4], 0)   # 外資淨買賣（張）
                        invest_net  = safe(row[7], 0)   # 投信淨買賣（張）
                        dealer_net  = safe(row[10], 0)  # 自營淨買賣（張）
                        result = {
                            "foreign_net": foreign_net,
                            "invest_net": invest_net,
                            "dealer_net": dealer_net,
                            "total_3inst": safe(row[11], 0),
                        }
                        cache_set(f"chip_{code}", result)
                        return result
            time.sleep(0.2)
    except Exception as e:
        print(f"[TWSE chip] {code}: {e}")
    return {}

# ===== 融資融券 =====

def get_margin(code):
    cached = cache_get(f"margin_{code}")
    if cached:
        return cached

    try:
        today = datetime.now()
        for i in range(5):
            d = (today - timedelta(days=i)).strftime("%Y%m%d")
            r = requests.get(TSE_MARGIN,
                params={"response":"json","date":d,"selectType":"ALL"},
                headers=MIS_HDR, timeout=8, verify=False)
            data = r.json()
            if data.get("stat") == "OK" and data.get("data"):
                for row in data["data"]:
                    if str(row[0]).strip() == code:
                        # 融資餘額、融資限額 → 使用率
                        margin_bal   = safe(row[2], 0)
                        margin_limit = safe(row[4], 1)
                        usage = round(margin_bal / margin_limit * 100, 1) if margin_limit else None
                        result = {"margin_pct": usage, "margin_bal": margin_bal}
                        cache_set(f"margin_{code}", result)
                        return result
            time.sleep(0.2)
    except Exception as e:
        print(f"[TWSE margin] {code}: {e}")
    return {}

# ===== 技術指標 =====

def calc_rsi(p, n=14):
    if len(p)<n+1: return None
    s=pd.Series(p); d=s.diff()
    g=d.clip(lower=0).rolling(n).mean(); l=-d.clip(upper=0).rolling(n).mean()
    rs=g/l; return safe((100-100/(1+rs)).iloc[-1],1)

def calc_macd(p):
    if len(p)<26: return "unknown"
    s=pd.Series(p)
    ml=s.ewm(span=12,adjust=False).mean()-s.ewm(span=26,adjust=False).mean()
    sg=ml.ewm(span=9,adjust=False).mean(); ht=ml-sg
    if ml.iloc[-2]<sg.iloc[-2] and ml.iloc[-1]>sg.iloc[-1]: return "bullish"
    if ml.iloc[-2]>sg.iloc[-2] and ml.iloc[-1]<sg.iloc[-1]: return "bearish"
    return "positive" if ht.iloc[-1]>0 else "negative"

def calc_kd(h,l,c,n=9):
    if len(c)<n: return None,None
    lm=pd.Series(l).rolling(n).min(); hm=pd.Series(h).rolling(n).max()
    rsv=(pd.Series(c)-lm)/(hm-lm).replace(0,np.nan)*100
    k=rsv.ewm(com=2,adjust=False).mean(); d=k.ewm(com=2,adjust=False).mean()
    return safe(k.iloc[-1],1), safe(d.iloc[-1],1)

def get_ma_state(p):
    if len(p)<20: return "unknown"
    s=pd.Series(p); cur=s.iloc[-1]
    m5=s.rolling(5).mean().iloc[-1]; m20=s.rolling(20).mean().iloc[-1]
    if len(p)>=60:
        m60=s.rolling(60).mean().iloc[-1]
        if cur>m5>m20>m60: return "all_above"
    if m5>m20 and cur>m20: return "golden_cross"
    if cur>m20: return "above_ma20"
    return "below_all"

def gen_signals(pe,yld,roe,gross,r,m,ms,foreign_net,invest_net):
    s=[]
    if m=="bullish":           s.append("MACD黃金交叉")
    if ms=="all_above":        s.append("多頭排列")
    elif ms=="golden_cross":   s.append("均線交叉")
    if r and r<30:             s.append("RSI超賣")
    if yld and yld>=4:         s.append("高殖利率")
    if pe and pe<12:           s.append("低本益比")
    if roe and roe>=20:        s.append("高ROE")
    if gross and gross>=40:    s.append("高毛利率")
    if foreign_net and foreign_net>1000:  s.append("外資買超")
    if invest_net and invest_net>100:     s.append("投信買超")
    return s[:5]

# ===== 核心查詢 =====

def query(code):
    code = code.strip().upper().replace(".TW","").replace(".TWO","")

    # 1. 即時報價
    mtype, rt = get_realtime(code)

    # 2. 歷史資料（帶量能）
    cl, hl, ll, vl = get_history(code)
    if not cl:
        return None, f"找不到股票：{code}"

    # 3. 補即時報價
    if not rt:
        price=cl[-1]; prev=cl[-2] if len(cl)>=2 else cl[-1]
        chg=round((price-prev)/prev*100,2) if prev else None
        rt={"name":code,"price":price,"prev_close":prev,"change":chg,
            "open":None,"high":None,"low":None,"volume":vl[-1] if vl else None}

    # 4. 財務面（Yahoo Finance）
    fin = get_financials(code, mtype)

    # 5. 本益比殖利率
    pe_data = get_pe_yield(code, mtype)
    pe      = pe_data.get("pe")
    yld     = pe_data.get("yield_pct")
    pb      = pe_data.get("pb")

    # 6. 籌碼面（上市才有三大法人公開資料）
    chip   = get_chip(code, mtype) if mtype == "tse" else {}
    margin = get_margin(code)      if mtype == "tse" else {}

    foreign_net = chip.get("foreign_net")
    invest_net  = chip.get("invest_net")
    dealer_net  = chip.get("dealer_net")
    margin_pct  = margin.get("margin_pct")

    # 7. 技術指標
    r_=calc_rsi(cl)    if len(cl)>=15 else None
    m_=calc_macd(cl)   if len(cl)>=26 else "unknown"
    k_,d_=calc_kd(hl,ll,cl)
    ms_=get_ma_state(cl)

    # 8. 走勢 + 量能（近 60 天）
    recent = min(60, len(cl))
    price_hist  = cl[-recent:]
    volume_hist = vl[-recent:] if vl else []

    high52 = fin.get("week_52_high") or (max(hl) if hl else None)
    low52  = fin.get("week_52_low")  or (min(ll) if ll else None)

    return {
        "code":code, "name":rt["name"], "market":"tw", "market_type":mtype or "tse",
        "price":rt["price"], "prev_close":rt.get("prev_close"), "change":rt.get("change"),
        "day_high":rt.get("high"), "day_low":rt.get("low"),
        "open":rt.get("open"), "volume":rt.get("volume"),
        "week_52_high":high52, "week_52_low":low52,
        # 基本面
        "pe":pe, "pb":pb, "yield_pct":yld,
        "roe":fin.get("roe"), "eps_growth":fin.get("eps_growth"),
        "revenue_growth":fin.get("revenue_growth"),
        "gross":fin.get("gross"), "net_margin":fin.get("net_margin"),
        "debt":fin.get("debt"), "cap":fin.get("cap","large"),
        "sector":fin.get("sector"),
        # 技術面
        "rsi":r_,"macd":m_,"kd":k_,"kd_d":d_,"ma_state":ms_,
        # 籌碼面
        "foreign_net":foreign_net,"invest_net":invest_net,"dealer_net":dealer_net,
        "foreign_days":None,"invest":None,"margin_pct":margin_pct,
        # 走勢+量
        "price_history":price_hist, "volume_history":volume_hist,
        # 訊號
        "signals":gen_signals(pe,yld,fin.get("roe"),fin.get("gross"),r_,m_,ms_,foreign_net,invest_net),
        "currency":"TWD","updated_at":datetime.now().isoformat()
    }, None

# ===== 路由 =====

@app.route("/")
def index():
    return jsonify({"status":"ok","message":"選股雷達 v11（完整版）",
                    "features":["財務面","技術面","籌碼面","快取"],
                    "time":datetime.now().isoformat()})

@app.route("/api/stock/<code>")
def get_stock(code):
    try:
        data,err=query(code)
        if err: return jsonify({"error":err}),404
        return jsonify(data)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error":str(e)}),500

@app.route("/api/cache/clear")
def clear_cache():
    _cache.clear()
    return jsonify({"status":"ok","message":"快取已清除"})

@app.route("/api/screen")
def screen_stocks():
    codes_param=request.args.get("codes","")
    if not codes_param: return jsonify({"error":"請提供股票代號"}),400
    codes=[c.strip().replace(".TW","").replace(".TWO","").upper()
           for c in codes_param.split(",") if c.strip()]
    if len(codes)>30: return jsonify({"error":"單次最多30檔"}),400
    results,errors=[],[]
    for code in codes:
        try:
            data,err=query(code)
            if err or not data: errors.append(code); continue
            results.append({
                "code":data["code"],"name":data["name"],"market":"tw",
                "price":data["price"],"change":data["change"],
                "pe":data["pe"],"yield_pct":data["yield_pct"],
                "roe":data["roe"],"gross":data["gross"],"debt":data["debt"],
                "rsi":data["rsi"],"macd":data["macd"],"kd":data["kd"],
                "ma_state":data["ma_state"],"cap":data["cap"],
                "foreign_net":data["foreign_net"],"invest_net":data["invest_net"],
                "margin_pct":data["margin_pct"],
                "signals":data["signals"],
            })
            time.sleep(0.3)
        except Exception as e:
            errors.append(code); print(f"[ERR]{code}:{e}")
    return jsonify({"results":results,"total":len(results),
                    "errors":errors,"updated_at":datetime.now().isoformat()})

if __name__ == "__main__":
    app.run(host="0.0.0.0",port=5000,debug=True)

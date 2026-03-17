"""
選股雷達 — Python Flask 後端 v7
上市用 TWSE，上櫃用 TPEx OpenAPI（更穩定）
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

MIS_URL   = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
TSE_HIST  = "https://www.twse.com.tw/exchangeReport/STOCK_DAY"
TSE_PE    = "https://www.twse.com.tw/exchangeReport/BWIBBU_d"
# TPEx OpenAPI — 更穩定，無需民國年轉換
OTC_HIST  = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
OTC_PE    = "https://www.tpex.org.tw/web/stock/aftertrading/peratio_analysis/pera_result.php"

HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
TSE_HDR = {**HDR, "Referer": "https://mis.twse.com.tw/"}
OTC_HDR = {**HDR, "Referer": "https://www.tpex.org.tw/"}

# ===== 工具 =====

def safe(v, d=2):
    if v is None or str(v).strip() in ("", "-", "--", "N/A"):
        return None
    try:
        f = float(str(v).replace(",", "").replace("+", ""))
        return None if (np.isnan(f) or np.isinf(f)) else round(f, d)
    except Exception:
        return None

# ===== 即時報價 =====

def get_realtime(code):
    for prefix in ["tse", "otc"]:
        try:
            r = requests.get(MIS_URL,
                params={"ex_ch": f"{prefix}_{code}.tw", "json": 1, "delay": 0},
                headers=TSE_HDR, timeout=8, verify=False)
            msg = r.json().get("msgArray", [])
            if msg and msg[0].get("n"):
                s = msg[0]
                price = safe(s.get("z")) or safe(s.get("y"))
                prev  = safe(s.get("y"))
                chg   = round((price-prev)/prev*100,2) if price and prev and prev!=0 else None
                return prefix, {
                    "name": s.get("n", code), "price": price, "prev_close": prev,
                    "change": chg, "open": safe(s.get("o")),
                    "high": safe(s.get("h")), "low": safe(s.get("l")),
                    "volume": safe(s.get("v"), 0),
                }
        except Exception as e:
            print(f"[MIS {prefix}] {code}: {e}")
        time.sleep(0.15)
    return None, None

# ===== 上市歷史（TWSE）=====

def tse_history(code, months=5):
    ac, ah, al = [], [], []
    for i in range(months):
        d = (datetime.now()-timedelta(days=30*i)).strftime("%Y%m01")
        try:
            r = requests.get(TSE_HIST,
                params={"response":"json","date":d,"stockNo":code},
                headers=TSE_HDR, timeout=8, verify=False)
            data = r.json()
            if data.get("stat") == "OK":
                for row in (data.get("data") or []):
                    c=safe(row[6]); h=safe(row[4]); l=safe(row[5])
                    if c: ac.append(c); ah.append(h or c); al.append(l or c)
            time.sleep(0.3)
        except Exception as e:
            print(f"[TSE hist] {code} {d}: {e}")
    ac.reverse(); ah.reverse(); al.reverse()
    return ac, ah, al

# ===== 上櫃歷史（TPEx OpenAPI）=====

def otc_history(code, months=5):
    """
    使用 TPEx OpenAPI 取得上櫃歷史資料
    每次取一個月，格式：YYYYMM
    回傳欄位：SecuritiesCompanyCode, Date, Close, Open, High, Low
    """
    ac, ah, al = [], [], []
    for i in range(months):
        t = datetime.now() - timedelta(days=30*i)
        yyyymm = t.strftime("%Y%m")
        try:
            r = requests.get(OTC_HIST,
                params={"date": yyyymm},
                headers=OTC_HDR, timeout=10, verify=False)
            rows = r.json()
            for row in rows:
                if str(row.get("SecuritiesCompanyCode","")).strip() == code:
                    c=safe(row.get("Close")); h=safe(row.get("High")); l=safe(row.get("Low"))
                    if c: ac.append(c); ah.append(h or c); al.append(l or c)
            time.sleep(0.3)
        except Exception as e:
            print(f"[OTC OpenAPI hist] {code} {yyyymm}: {e}")

    # 若 OpenAPI 查不到，退回舊版 API
    if not ac:
        ac, ah, al = otc_history_fallback(code, months)

    ac.reverse(); ah.reverse(); al.reverse()
    return ac, ah, al

def otc_history_fallback(code, months=5):
    """TPEx 舊版 API 備援"""
    ac, ah, al = [], [], []
    for i in range(months):
        t = datetime.now()-timedelta(days=30*i)
        d = f"{t.year-1911}/{t.month:02d}"
        try:
            url = "https://www.tpex.org.tw/web/stock/aftertrading/daily_trading_info/st43_result.php"
            r = requests.get(url,
                params={"d":d,"stkno":code,"s":"0,asc,0","l":"zh-tw","o":"json"},
                headers=OTC_HDR, timeout=8, verify=False)
            rows = r.json().get("aaData") or []
            for row in rows:
                # 欄位：日期(0)、成交股數(1)、成交金額(2)、開盤(3)、最高(4)、最低(5)、收盤(6)
                c=safe(row[6]); h=safe(row[4]); l=safe(row[5])
                if c: ac.append(c); ah.append(h or c); al.append(l or c)
            time.sleep(0.3)
        except Exception as e:
            print(f"[OTC fallback] {code} {d}: {e}")
    return ac, ah, al

# ===== 本益比殖利率 =====

def tse_pe(code, months=5):
    for i in range(months):
        d = (datetime.now()-timedelta(days=30*i)).strftime("%Y%m01")
        try:
            r = requests.get(TSE_PE,
                params={"response":"json","date":d,"stockNo":code},
                headers=TSE_HDR, timeout=8, verify=False)
            data = r.json()
            if data.get("stat") == "OK":
                for row in reversed(data.get("data") or []):
                    pe=safe(row[3]); yld=safe(row[1]); pb=safe(row[5])
                    if pe or yld: return pe, yld, pb
            time.sleep(0.25)
        except Exception as e:
            print(f"[TSE PE] {code}: {e}")
    return None, None, None

def otc_pe(code, months=5):
    for i in range(months):
        t = datetime.now()-timedelta(days=30*i)
        d = f"{t.year-1911}/{t.month:02d}"
        try:
            r = requests.get(OTC_PE,
                params={"d":d,"stkno":code,"l":"zh-tw","o":"json"},
                headers=OTC_HDR, timeout=8, verify=False)
            rows = r.json().get("aaData") or []
            for row in reversed(rows):
                pe=safe(row[1]); yld=safe(row[3]); pb=safe(row[4])
                if pe or yld: return pe, yld, pb
            time.sleep(0.25)
        except Exception as e:
            print(f"[OTC PE] {code}: {e}")
    return None, None, None

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

def gen_signals(pe,yld,r,m,ms):
    s=[]
    if m=="bullish":       s.append("MACD黃金交叉")
    if ms=="all_above":    s.append("多頭排列")
    elif ms=="golden_cross": s.append("均線交叉")
    if r and r<30:         s.append("RSI超賣")
    if yld and yld>=4:     s.append("高殖利率")
    if pe and pe<12:       s.append("低本益比")
    if r and 50<r<70:      s.append("強勢區間")
    return s[:4]

# ===== 核心查詢 =====

def query(code):
    code = code.strip().upper().replace(".TW","").replace(".TWO","")

    mtype, rt = get_realtime(code)

    if mtype == "otc":
        cl, hl, ll = otc_history(code)
    elif mtype == "tse":
        cl, hl, ll = tse_history(code)
    else:
        cl, hl, ll = tse_history(code)
        if not cl:
            cl, hl, ll = otc_history(code)
            mtype = "otc" if cl else None
        else:
            mtype = "tse"

    if not cl:
        return None, f"找不到股票：{code}"

    if not rt:
        price=cl[-1]; prev=cl[-2] if len(cl)>=2 else cl[-1]
        chg=round((price-prev)/prev*100,2) if prev else None
        rt={"name":code,"price":price,"prev_close":prev,"change":chg,
            "open":None,"high":None,"low":None,"volume":None}

    time.sleep(0.2)
    pe,yld,pb = otc_pe(code) if mtype=="otc" else tse_pe(code)

    r_=calc_rsi(cl) if len(cl)>=15 else None
    m_=calc_macd(cl) if len(cl)>=26 else "unknown"
    k_,d_=calc_kd(hl,ll,cl)
    ms_=get_ma_state(cl)

    return {
        "code":code, "name":rt["name"], "market":"tw", "market_type":mtype or "tse",
        "price":rt["price"], "prev_close":rt.get("prev_close"), "change":rt.get("change"),
        "day_high":rt.get("high"), "day_low":rt.get("low"),
        "open":rt.get("open"), "volume":rt.get("volume"),
        "week_52_high":max(hl) if hl else None, "week_52_low":min(ll) if ll else None,
        "pe":pe, "pb":pb, "yield_pct":yld,
        "roe":None,"eps_growth":None,"gross":None,"debt":None,"cap":"large",
        "rsi":r_,"macd":m_,"kd":k_,"kd_d":d_,"ma_state":ms_,
        "foreign_days":None,"invest":None,"margin_pct":None,
        "price_history":cl[-30:],
        "signals":gen_signals(pe,yld,r_,m_,ms_),
        "sector":None,"currency":"TWD","updated_at":datetime.now().isoformat()
    }, None

# ===== 路由 =====

@app.route("/")
def index():
    return jsonify({"status":"ok","message":"選股雷達 v7（上市+上櫃）","time":datetime.now().isoformat()})

@app.route("/api/stock/<code>")
def get_stock(code):
    try:
        data,err=query(code)
        if err: return jsonify({"error":err}),404
        return jsonify(data)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error":str(e)}),500

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
                "roe":None,"gross":None,"debt":None,
                "rsi":data["rsi"],"macd":data["macd"],"kd":data["kd"],"ma_state":data["ma_state"],
                "cap":"large","foreign_days":None,"invest":None,"margin_pct":None,
                "signals":data["signals"],
            })
            time.sleep(0.8)
        except Exception as e:
            errors.append(code); print(f"[ERR]{code}:{e}")
    return jsonify({"results":results,"total":len(results),
                    "errors":errors,"updated_at":datetime.now().isoformat()})

if __name__ == "__main__":
    app.run(host="0.0.0.0",port=5000,debug=True)

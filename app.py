import streamlit as st
import pandas as pd
import yfinance as yf
from streamlit_gsheets import GSheetsConnection
from datetime import datetime
import time
import pytz
from concurrent.futures import ThreadPoolExecutor
import requests
import io

# --- CONFIG ---
st.set_page_config(page_title="EP Dual-Engine Cockpit", layout="wide")
st.title("🛡️ EP Strategy: Intraday 5X vs. Stage 2 Swing")

def get_now_ist():
    return datetime.now(pytz.timezone('Asia/Kolkata'))

def is_market_open():
    now = get_now_ist()
    if now.weekday() >= 5: return False
    mkt_start = now.replace(hour=9, minute=15, second=0, microsecond=0)
    mkt_end = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return mkt_start <= now <= mkt_end

conn = st.connection("gsheets", type=GSheetsConnection)

# --- DATA ENGINES ---
def get_vwap_data(sym):
    try:
        t = yf.Ticker(f"{sym}.NS")
        df = t.history(period="1d", interval="1m")
        if df.empty or df['Volume'].sum() == 0: return 0.0, 0.0, 0.0
        df['TP'] = (df['High'] + df['Low'] + df['Close']) / 3
        vol_sum = df['Volume'].sum()
        vwap = (df['TP'] * df['Volume']).sum() / vol_sum
        ltp = df['Close'].iloc[-1]
        return round(ltp, 2), round(vwap, 2), 0.0
    except: return 0.0, 0.0, 0.0

def get_swing_stops(sym):
    try:
        t = yf.Ticker(f"{sym}.NS")
        hist = t.history(period="50d")
        if hist.empty: return 0.0, 0.0, 0.0
        hard_sl = hist['Low'].iloc[-1]
        trail_sl = hist['Close'].rolling(20).mean().iloc[-1]
        curr_ltp = hist['Close'].iloc[-1]
        return round(hard_sl, 2), round(trail_sl, 2), round(curr_ltp, 2)
    except: return 0.0, 0.0, 0.0

def process_ticker(sym, threshold, use_sma_wall):
    try:
        t = yf.Ticker(f"{sym}.NS")
        hist = t.history(period="1y")
        if len(hist) < 200: return None
        curr_p = hist['Close'].iloc[-1]
        sma200 = hist['Close'].rolling(200).mean().iloc[-1]
        if use_sma_wall and curr_p < (sma200 * 0.98): return None
        
        prev_c = hist['Close'].iloc[-2]
        day_h = hist['High'].iloc[-1]
        max_chg = ((day_h - prev_c) / prev_c) * 100
        avg_vol = hist['Volume'].tail(30).mean()
        rvol = hist['Volume'].iloc[-1] / (avg_vol if avg_vol > 0 else 1)
        
        if max_chg >= threshold and rvol > 1.2:
            _, vwap, _ = get_vwap_data(sym)
            if vwap == 0.0: vwap = curr_p 
            if curr_p < vwap: return None
            sys_sl = round(vwap - 2.0, 2)
            sl_drop_pct = round(((curr_p - sys_sl) / curr_p) * 100, 2)
            
            return {
                'Symbol': sym, 'LTP': round(curr_p, 2), 
                'Max%': round(max_chg, 2), 'RVOL': round(rvol, 1), 
                'Dist_Wall%': round(((curr_p - sma200) / sma200) * 100, 2),
                'Sys_SL': sys_sl, 'SL_Distance%': f"-{sl_drop_pct}%"
            }
    except: pass
    return None

def run_engine(threshold, use_sma_wall, universe="Nifty 500"):
    urls = {
        "Nifty 500": ["https://archives.nseindia.com/content/indices/ind_nifty500list.csv"],
        "Microcap 250": ["https://archives.nseindia.com/content/indices/ind_niftymicrocap250list.csv"]
    }
    targets = urls.get(universe, urls["Nifty 500"])
    tickers = []
    headers = {'User-Agent': 'Mozilla/5.0'}
    for url in targets:
        try:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                tickers = pd.read_csv(io.StringIO(response.text))['Symbol'].tolist()
                if tickers: break
        except: continue
    
    results = []
    prog = st.progress(0)
    with ThreadPoolExecutor(max_workers=25) as executor:
        futures = [executor.submit(process_ticker, s, threshold, use_sma_wall) for s in tickers]
        for i, f in enumerate(futures):
            prog.progress((i+1)/len(tickers))
            res = f.result()
            if res: results.append(res)
    prog.empty()
    df = pd.DataFrame(results)
    if not df.empty:
        df = df.sort_values(by='Max%', ascending=False).reset_index(drop=True)
        df['Rank'] = "Laggard"

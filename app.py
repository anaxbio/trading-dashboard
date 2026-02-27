import streamlit as st
import pandas as pd
import yfinance as yf
from streamlit_gsheets import GSheetsConnection
from datetime import datetime
import time
import pytz
from concurrent.futures import ThreadPoolExecutor

# --- CONFIG ---
st.set_page_config(page_title="EP Dual-Engine Cockpit", layout="wide")
st.title("🛡️ EP Strategy: Intraday 5X vs. Stage 2 Swing")

def get_now_ist():
    return datetime.now(pytz.timezone('Asia/Kolkata'))

conn = st.connection("gsheets", type=GSheetsConnection)

# --- DATA ENGINES ---
def get_vwap_data(sym):
    """Fetches 1m data to calculate precise intraday VWAP."""
    try:
        t = yf.Ticker(f"{sym}.NS")
        df = t.history(period="1d", interval="1m")
        if df.empty or df['Volume'].sum() == 0: return 0.0, 0.0
        df['TP'] = (df['High'] + df['Low'] + df['Close']) / 3
        vwap = (df['TP'] * df['Volume']).sum() / df['Volume'].sum()
        return round(df['Close'].iloc[-1], 2), round(vwap, 2)
    except: return 0.0, 0.0

def process_ticker(sym, threshold, use_sma_wall):
    """Core logic for both scanners."""
    try:
        t = yf.Ticker(f"{sym}.NS")
        hist = t.history(period="1y")
        if len(hist) < 200: return None
        
        curr_p = hist['Close'].iloc[-1]
        sma200 = hist['Close'].rolling(200).mean().iloc[-1]
        dist_from_wall = ((curr_p - sma200) / sma200) * 100
        
        # Strictly apply the Wall for Swing tab
        if use_sma_wall and curr_p < sma200: return None
        
        prev_c = hist['Close'].iloc[-2]
        day_h = hist['High'].iloc[-1]
        max_chg = ((day_h - prev_c) / prev_c) * 100
        
        avg_vol = hist['Volume'].tail(30).mean()
        rvol = hist['Volume'].iloc[-1] / avg_vol
        
        if max_chg >= threshold and rvol > 1.5:
            return {
                'Symbol': sym, 
                'LTP': round(curr_p, 2), 
                'Max%': round(max_chg, 2), 
                'RVOL': round(rvol, 1),
                'Dist_Wall%': round(dist_from_wall, 2)
            }
    except: pass
    return None

def run_engine(threshold, use_sma_wall):
    """Orchestrates the Nifty 500 scan."""
    url = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
    try:
        tickers = pd.read_csv(url)['Symbol'].tolist()
    except: return pd.DataFrame()
    
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
    if not df.empty and use_sma_wall:
        # Ranking Logic for Swing Tab
        df = df.sort_values(by='Dist_Wall%', ascending=False).reset_index(drop=True)
        df['Rank'] = "Laggard"
        df.loc[0:4, 'Rank'] = "🔥 LEADER"
        df.loc[5:7, 'Rank'] = "⚠️ LAGGARD"
        df = df.head(8)
    return df

# --- UI TABS ---
tab1, tab2 = st.tabs(["🚀 INTRADAY 5X COCKPIT", "📈 STAGE 2 SWING"])

# --- TAB 1: INTRADAY 5X ---
with tab1:
    st.subheader("Step 1: Intraday Hunter (No Wall)")
    if st.button("🔥 Scan Intraday Movers (>4%)"):
        st.session_state.intra_results = run_engine(4.0, use_sma_wall=False)
    
    if 'intra_results' in st

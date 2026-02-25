import streamlit as st
import pandas as pd
import yfinance as yf
from streamlit_gsheets import GSheetsConnection
from datetime import datetime
import time
import pytz

# --- CONFIG ---
st.set_page_config(page_title="EP Monitor", layout="wide")
st.title("🚀 EP Stage 2 Tracker")

# Helper function for IST Time
def get_now_ist():
    ist = pytz.timezone('Asia/Kolkata')
    return datetime.now(ist)

if 'scan_results' not in st.session_state:
    st.session_state.scan_results = pd.DataFrame()

conn = st.connection("gsheets", type=GSheetsConnection)

# --- CORE FUNCTIONS ---
def get_2min_strategy_data(symbol):
    ticker_sym = str(symbol).strip().upper()
    if not ticker_sym.endswith(".NS"): ticker_sym += ".NS"
    try:
        df = yf.download(ticker_sym, period="1d", interval="2m", progress=False)
        if not df.empty:
            tp = (df['High'] + df['Low'] + df['Close']) / 3
            vwap = (tp * df['Volume']).cumsum() / df['Volume'].cumsum()
            return {'LTP': float(df['Close'].iloc[-1]), 'VWAP': float(vwap.iloc[-1])}
    except: pass
    return {'LTP': 0.0, 'VWAP': 0.0}

def run_scan(threshold):
    url = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
    try:
        tickers = pd.read_csv(url)['Symbol'].tolist()
    except: return pd.DataFrame()
    results = []
    prog = st.progress(0)
    for i, sym in enumerate(tickers[:120]):
        prog.progress(i / 120)
        try:
            t = yf.Ticker(f"{sym}.NS")
            hist = t.history(period="1y")
            if len(hist) > 200:
                sma200 = hist['Close'].rolling(200).mean().iloc[-1]
                curr_p = hist['Close'].iloc[-1]
                prev_c = hist['Close'].iloc[-2]
                day_chg = ((curr_p - prev_c) / prev_c) * 100
                if curr_p > (sma200 * 0.98) and day_chg >= threshold:
                    results.append({'Symbol': sym, 'Entry_Price': round(curr_p, 2), 'Day %': round(day_chg, 2)})
        except: continue
    prog.empty()
    return pd.DataFrame(results)

# --- UI TABS ---
tab1, tab2, tab3 = st.tabs(["🚀 Scanner", "💰 Intraday", "📈 Swing"])

with tab1:
    st.header("Step 1: Daily Scanner")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("🎯 Run 5% Scan"): st.session_state.scan_results = run_scan(5.0)
    with c2:
        if st.button("✅ Run 3.5% Scan"): st.session_state.scan_results = run_scan(3.5)

    if not st.session_state.scan_results.empty:
        st.subheader("Scan Results")
        confirmed = []
        for i, row in st.session_state.scan_results.iterrows():
            # Using unique keys for checkboxes to avoid conflict
            if st.checkbox(f"Add {row['Symbol']} (@ {row['Entry_Price']})", key=f"scan_{row['Symbol']}"):
                confirmed.append({
                    'Symbol': row['Symbol'], 
                    'Entry_Price': row['Entry_Price'], 
                    'Update_Time': get_now_ist().strftime('%Y-%m-%d %H:%M:%S'),
                    'Status': 'OPEN'
                })
        
        if confirmed:
            st.divider()
            mode = st.radio("Target Portfolio:", ["INTRADAY_PORTFOLIO", "SWING_PORTFOLIO"])
            # THE RESTORED COMMIT BUTTON

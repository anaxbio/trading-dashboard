import streamlit as st
import pandas as pd
import yfinance as yf
from streamlit_gsheets import GSheetsConnection
from datetime import datetime
import time
import pytz

# --- CONFIG ---
st.set_page_config(page_title="EP Monitor", layout="wide")
st.title("🚀 EP Stage 2 Dashboard")

def get_now_ist():
    return datetime.now(pytz.timezone('Asia/Kolkata'))

if 'scan_results' not in st.session_state:
    st.session_state.scan_results = pd.DataFrame()

conn = st.connection("gsheets", type=GSheetsConnection)

def get_2min_strategy_data(symbol):
    ticker_sym = str(symbol).strip().upper()
    if not ticker_sym.endswith(".NS"): ticker_sym += ".NS"
    try:
        # We fetch 1m/2m data for the most accurate current state
        df = yf.download(ticker_sym, period="1d", interval="2m", progress=False)
        if not df.empty:
            tp = (df['High'] + df['Low'] + df['Close']) / 3
            vwap = (tp * df['Volume']).cumsum() / df['Volume'].cumsum()
            return {
                'LTP': float(df['Close'].iloc[-1]), 
                'VWAP': float(vwap.iloc[-1]),
                'Day_High': float(df['High'].max())
            }
    except: pass
    return {'LTP': 0.0, 'VWAP': 0.0, 'Day_High': 0.0}

def run_scan(threshold):
    url = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
    try:
        tickers = pd.read_csv(url)['Symbol'].tolist()
    except: return pd.DataFrame()
    results = []
    prog = st.progress(0)
    total = len(tickers)
    
    for i, sym in enumerate(tickers):
        prog.progress(i / total)
        try:
            t = yf.Ticker(f"{sym}.NS")
            # Fetch 2 days to compare High vs Prev Close
            hist = t.history(period="2d") 
            if len(hist) >= 2:
                prev_close = hist['Close'].iloc[-2]
                day_high = hist['High'].iloc[-1]
                curr_price = hist['Close'].iloc[-1]
                
                # Check Stage 2 (Price > 200 SMA)
                full_hist = t.history(period="1y")
                sma200 = full_hist['Close'].rolling(200).mean().iloc[-1]
                
                # SENSE CHECK: Did it hit the threshold at ANY point today?
                max_chg = ((day_high - prev_close) / prev_close) * 100
                curr_chg = ((curr_price - prev_close) / prev_close) * 100
                
                if curr_price > (sma200 * 0.98) and max_chg >= threshold:
                    results.append({
                        'Symbol': sym, 
                        'Entry_Price': round(curr_price, 2), 
                        'Day_High%': round(max_chg, 2),
                        'Current%': round(curr_chg, 2)
                    })
        except: continue
    prog.empty()
    return pd.DataFrame(results)

# --- TABS (Rest of the code remains the same as your "Solid" build) ---
tab1, tab2, tab3 = st.tabs(["🚀 Scanner", "💰 Intraday", "📈 Swing"])

with tab1:
    st.header("Step 1: Daily Scanner")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("🎯 Run 5% Primary Scan"):
            st.session_state.scan_results = run_scan(5.0)
    with c2:
        if st.button("✅ Run 3.5% Strength Scan"):
            st.session_state.scan_results = run_scan(3.5)

    if not st.session_state.scan_results.empty:
        st.subheader("Selection & Commit")
        with st.form("commit_form"):
            confirmed = []
            # We display both Current % and Day High % so you see the "Episode"
            st.dataframe(st.session_state.scan_results) 
            for i, row in st.session_state.scan_results.iterrows():
                if st.checkbox(f"Add {row['Symbol']}", key=f"s_{row['Symbol']}"):
                    confirmed.append({
                        'Symbol': row['Symbol'], 'Entry_Price': row['Entry_Price'], 
                        'Date': get_now_ist().strftime('%Y-%m-%d %H:%M:%S'), 'Status': 'OPEN'
                    })
            mode = st.radio("Target Portfolio:", ["INTRADAY_PORTFOLIO", "SWING_PORTFOLIO"])
            if st.form_submit_button("💾 COMMIT SELECTED TRADES"):
                if confirmed:
                    try:
                        df = conn.read(worksheet=mode, ttl=0).dropna(how='all')
                        updated = pd.concat([df, pd.DataFrame(confirmed)], ignore_index=True)
                        conn.update(worksheet=mode, data=updated)
                        st.success("Synced and Committed!")
                        st.session_state.scan_results = pd.DataFrame()
                        time.sleep(1); st.rerun()
                    except Exception as e: st.error(f"Error: {e}")

# (Tabs 2 and 3 remain as per your existing code)

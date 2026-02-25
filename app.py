import streamlit as st
import pandas as pd
import requests
import yfinance as yf
from streamlit_gsheets import GSheetsConnection
from datetime import datetime

# --- CONFIG ---
st.set_page_config(page_title="EP Monitor", layout="wide")
st.title("🚀 EP Stage 2 Tracker")

if 'scan_results' not in st.session_state:
    st.session_state.scan_results = pd.DataFrame()

# Connection (Uses Streamlit Secrets)
conn = st.connection("gsheets", type=GSheetsConnection)

def get_2min_strategy_data(symbol):
    """Calculates Strategy VWAP using 2-minute candle closes."""
    ticker_sym = str(symbol).strip().upper()
    if not ticker_sym.endswith(".NS"): ticker_sym += ".NS"
    
    try:
        # Fetch 2m interval data for the current day
        df = yf.download(ticker_sym, period="1d", interval="2m", progress=False)
        
        if not df.empty:
            # Calculate Cumulative VWAP: sum(TP * Vol) / sum(Vol)
            tp = (df['High'] + df['Low'] + df['Close']) / 3
            tpv = tp * df['Volume']
            current_vwap = tpv.cumsum() / df['Volume'].cumsum()
            
            return {
                'LTP': round(df['Close'].iloc[-1], 2), 
                'VWAP': round(current_vwap.iloc[-1], 2)
            }
    except:
        pass
    return {'LTP': 0, 'VWAP': 0}

def run_scan(threshold):
    """Standard Stage 2 Scanner."""
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
                prev_c = hist['Close'].iloc[-2]
                curr_p = hist['Close'].iloc[-1]
                day_chg = ((curr_p - prev_c) / prev_c) * 100
                if curr_p > (sma200 * 0.98) and day_chg >= threshold:
                    results.append({'Symbol': sym, 'Entry': round(curr_p, 2), 'Day %': round(day_chg, 2)})
        except: continue
    prog.empty()
    return pd.DataFrame(results)

# --- TABS ---
tab1, tab2, tab3 = st.tabs(["🚀 Scanner", "💰 Intraday", "📈 Swing"])

with tab1:
    st.header("Step 1: Scanner")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("🎯 Run 5% Scan"):
            st.session_state.scan_results = run_scan(5.0)
    with c2:
        if st.button("✅ Run 3.5% Scan"):
            st.session_state.scan_results = run_scan(3.5)

    if not st.session_state.scan_results.empty:
        confirmed = []
        for i, row in st.session_state.scan_results.iterrows():
            if st.checkbox(f"Add {row['Symbol']} (@ {row['Entry']})", key=f"c_{row['Symbol']}"):
                confirmed.append({'Symbol': row['Symbol'], 'Entry': row['Entry'], 'Date': datetime.now().strftime('%Y-%m-%d'), 'Status': 'OPEN'})
        
        if confirmed:
            mode = st.radio("Portfolio:", ["INTRADAY_PORTFOLIO", "SWING_PORTFOLIO"])
            if st.button("💾 Save to Ledger"):
                try:
                    try:
                        old_df = conn.read(worksheet=mode, ttl=0)
                    except:
                        old_df = pd.DataFrame(columns=['Symbol', 'Entry', 'Date', 'Status'])
                    updated = pd.concat([old_df, pd.DataFrame(confirmed)], ignore_index=True).drop_duplicates(subset=['Symbol', 'Date'])
                    conn.update(worksheet=mode, data=updated)
                    st.success("Saved!")
                    st.session_state.scan_results = pd.DataFrame()
                    st.rerun()
                except Exception as e: st.error(f"Error: {e}")

with tab2:
    st.header("Intraday Monitor (2m Close vs VWAP)")
    if st.button("🔄 Refresh Intraday", key="ri"): st.rerun()
    try:
        df_i = conn.read(worksheet="INTRADAY_PORTFOLIO", ttl=0)
        if not df_i.empty:
            active_i = df_i[df_i['Status'].astype(str).str.upper().str.strip() == 'OPEN'].copy()
            if not active_i.empty:
                ltps, vwaps, signals = [], [], []
                for s in active_i['Symbol']:
                    stats = get_2min_strategy_data(s)
                    ltps.append(stats['LTP'])
                    vwaps.append(stats['VWAP'])
                    signals.append("🚨 EXIT" if stats['LTP'] < stats['VWAP'] and stats['LTP'] > 0 else "✅ OK")
                active_i['2m Close'], active_i['VWAP'], active_i['Signal'] = ltps, vwaps, signals
                st.table(active_i[['Symbol', 'Entry', '2m Close', 'VWAP', 'Signal']])
    except: st.info("Intraday ledger empty.")

with tab3:
    st.header("Swing Monitor (7% SL)")
    if st.button("🔄 Refresh Swing", key="rs"): st.rerun()
    try:
        df_s = conn.read(worksheet="SWING_PORTFOLIO", ttl=0)
        if not df_s.empty:
            active_s = df_s[df_s['Status'].astype(str).str.upper().str.strip() == 'OPEN'].copy()
            if not active_s.empty:
                ltps_s, signals_s = [], []
                for idx, row in active_s.iterrows():
                    curr = get_2min_strategy_data(row['Symbol'])['LTP']
                    sl = float(row['Entry']) * 0.93
                    ltps_s.append(curr)
                    signals_s.append("🚨 SELL" if curr < sl and curr > 0 else "✅ OK")
                active_s['Price'], active_s['Signal'] = ltps_s, signals_s
                st.table(active_s[['Symbol', 'Entry', 'Price', 'Signal']])
    except: st.info("Swing ledger empty.")

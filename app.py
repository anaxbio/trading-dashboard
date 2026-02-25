import streamlit as st
import pandas as pd
import yfinance as yf
from streamlit_gsheets import GSheetsConnection
from datetime import datetime

# --- CONFIG ---
st.set_page_config(page_title="EP Monitor", layout="wide")
st.title("🚀 EP Stage 2 Tracker")

# Initializing Session State
if 'scan_results' not in st.session_state:
    st.session_state.scan_results = pd.DataFrame()

conn = st.connection("gsheets", type=GSheetsConnection)

def get_2min_strategy_data(symbol):
    """Fetches 2-minute candle close and calculates Cumulative VWAP."""
    ticker_sym = str(symbol).strip().upper()
    if not ticker_sym.endswith(".NS"): ticker_sym += ".NS"
    try:
        df = yf.download(ticker_sym, period="1d", interval="2m", progress=False)
        if not df.empty:
            tp = (df['High'] + df['Low'] + df['Close']) / 3
            vwap = (tp * df['Volume']).cumsum() / df['Volume'].cumsum()
            return {'LTP': round(df['Close'].iloc[-1], 2), 'VWAP': round(vwap.iloc[-1], 2)}
    except: pass
    return {'LTP': 0, 'VWAP': 0}

def get_or_create_sheet(sheet_name, columns):
    """Ensures the worksheet exists and has the correct headers."""
    try:
        return conn.read(worksheet=sheet_name, ttl=0).dropna(how='all')
    except:
        # Create a blank sheet with headers if it doesn't exist
        new_df = pd.DataFrame(columns=columns)
        conn.update(worksheet=sheet_name, data=new_df)
        return new_df

# --- TABS ---
tab1, tab2, tab3 = st.tabs(["🚀 Scanner", "💰 Intraday", "📈 Swing"])

with tab1:
    st.header("Step 1: Daily Scanner")
    # ... [Your working Scanner logic here] ...
    # (Simplified for brevity, assuming run_scan is defined)
    if st.button("🎯 Run 5% Primary Scan"):
        # Placeholder for run_scan
        st.session_state.scan_results = pd.DataFrame([{'Symbol': 'TATASTEEL', 'Entry': 145.2, 'Day %': 5.2}]) 

    if not st.session_state.scan_results.empty:
        confirmed = []
        for i, row in st.session_state.scan_results.iterrows():
            if st.checkbox(f"Add {row['Symbol']} (@ {row['Entry']})", key=f"c_{row['Symbol']}"):
                confirmed.append({'Symbol': row['Symbol'], 'Entry_Price': row['Entry'], 'Date': datetime.now().strftime('%Y-%m-%d'), 'Status': 'OPEN'})
        
        if confirmed:
            mode = st.radio("Target Portfolio:", ["INTRADAY_PORTFOLIO", "SWING_PORTFOLIO"])
            if st.button("💾 Commit to Ledger"):
                headers = ['Symbol', 'Entry_Price', 'Date', 'Status']
                df = get_or_create_sheet(mode, headers)
                updated = pd.concat([df, pd.DataFrame(confirmed)], ignore_index=True).drop_duplicates()
                conn.update(worksheet=mode, data=updated)
                st.success(f"Saved to {mode}!")
                st.session_state.scan_results = pd.DataFrame()
                st.rerun()

with tab2:
    st.header("Intraday Monitor")
    if st.button("🔄 Refresh Intraday"): st.rerun()
    df_i = get_or_create_sheet("INTRADAY_PORTFOLIO", ['Symbol', 'Entry_Price', 'Date', 'Status'])
    if not df_i.empty:
        active_i = df_i[df_i['Status'].astype(str).str.upper().str.strip() == 'OPEN'].copy()
        if not active_i.empty:
            results = [get_2min_strategy_data(s) for s in active_i['Symbol']]
            active_i['2m Close'] = [r['LTP'] for r in results]
            active_i['VWAP'] = [r['VWAP'] for r in results]
            active_i['Signal'] = ["🚨 EXIT" if r['LTP'] < r['VWAP'] and r['LTP'] > 0 else "✅ OK" for r in results]
            st.table(active_i)

with tab3:
    st.header("Swing Monitor")
    if st.button("🔄 Refresh Swing"): st.rerun()
    df_s = get_or_create_sheet("SWING_PORTFOLIO", ['Symbol', 'Entry_Price', 'Date', 'Status'])
    if not df_s.empty:
        active_s = df_s[df_s['Status'].astype(str).str.upper().str.strip() == 'OPEN'].copy()
        if not active_s.empty:
            prices = [get_2min_strategy_data(s)['LTP'] for s in active_s['Symbol']]
            active_s['Price'] = prices
            active_s['Signal'] = ["🚨 SELL" if (p < float(e)*0.93 and p > 0) else "✅ OK" for p, e in zip(prices, active_s['Entry_Price'])]
            st.table(active_s)

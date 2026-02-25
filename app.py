import streamlit as st
import pandas as pd
import yfinance as yf
from streamlit_gsheets import GSheetsConnection
from datetime import datetime

# --- CONFIG ---
st.set_page_config(page_title="EP Monitor", layout="wide")
st.title("🚀 EP Stage 2 Tracker")

if 'scan_results' not in st.session_state:
    st.session_state.scan_results = pd.DataFrame()

conn = st.connection("gsheets", type=GSheetsConnection)

def get_2min_strategy_data(symbol):
    """Fetches 2-minute candle data and calculates VWAP."""
    ticker_sym = str(symbol).strip().upper()
    if not ticker_sym.endswith(".NS"): ticker_sym += ".NS"
    try:
        df = yf.download(ticker_sym, period="1d", interval="2m", progress=False)
        if not df.empty:
            # VWAP Calculation
            tp = (df['High'] + df['Low'] + df['Close']) / 3
            vwap = (tp * df['Volume']).cumsum() / df['Volume'].cumsum()
            return {'LTP': round(df['Close'].iloc[-1], 2), 'VWAP': round(vwap.iloc[-1], 2)}
    except:
        pass
    return {'LTP': 0, 'VWAP': 0}

# --- TABS ---
tab1, tab2, tab3 = st.tabs(["🚀 Scanner", "💰 Intraday", "📈 Swing"])

with tab1:
    st.header("Step 1: Scanner")
    # [Scanner code here - ensuring 'run_scan' is defined above or imported]
    # For now, let's assume the scanner works and focuses on the Commit logic:
    
    if not st.session_state.scan_results.empty:
        confirmed = []
        for i, row in st.session_state.scan_results.iterrows():
            if st.checkbox(f"Add {row['Symbol']} (@ {row['Entry']})", key=f"c_{row['Symbol']}"):
                confirmed.append({'Symbol': row['Symbol'], 'Entry_Price': row['Entry'], 'Date': datetime.now().strftime('%Y-%m-%d'), 'Status': 'OPEN'})
        
        if confirmed:
            mode = st.radio("Target Portfolio:", ["INTRADAY_PORTFOLIO", "SWING_PORTFOLIO"])
            if st.button("💾 Commit to Ledger"):
                try:
                    # Read existing data
                    df = conn.read(worksheet=mode, ttl=0).dropna(how='all')
                    updated = pd.concat([df, pd.DataFrame(confirmed)], ignore_index=True).drop_duplicates()
                    conn.update(worksheet=mode, data=updated)
                    st.success(f"Saved to {mode}!")
                    st.session_state.scan_results = pd.DataFrame()
                    st.rerun()
                except Exception as e:
                    st.error(f"Make sure the tab '{mode}' exists in your Google Sheet!")

with tab2:
    st.header("Intraday Monitor")
    try:
        df_i = conn.read(worksheet="INTRADAY_PORTFOLIO", ttl=0).dropna(how='all')
        if not df_i.empty:
            # Cleaning the Status column
            df_i['Status'] = df_i['Status'].astype(str).str.upper().str.strip()
            active_i = df_i[df_i['Status'] == 'OPEN'].copy()
            
            if not active_i.empty:
                ltps, vwaps, signals = [], [], []
                for s in active_i['Symbol']:
                    res = get_2min_strategy_data(s)
                    ltps.append(res['LTP'])
                    vwaps.append(res['VWAP'])
                    signals.append("🚨 EXIT" if res['LTP'] < res['VWAP'] and res['LTP'] > 0 else "✅ OK")
                
                active_i['2m Close'] = ltps
                active_i['VWAP'] = vwaps
                active_i['Signal'] = signals
                st.table(active_i)
            else:
                st.info("No active 'OPEN' trades in Intraday.")
    except:
        st.warning("Please create a tab named 'INTRADAY_PORTFOLIO' in your Google Sheet.")

with tab3:
    st.header("Swing Monitor")
    try:
        df_s = conn.read(worksheet="SWING_PORTFOLIO", ttl=0).dropna(how='all')
        if not df_s.empty:
            df_s['Status'] = df_s['Status'].astype(str).str.upper().str.strip()
            active_s = df_s[df_s['Status'] == 'OPEN'].copy()
            
            if not active_s.empty:
                prices = [get_2min_strategy_data(s)['LTP'] for s in active_s['Symbol']]
                active_s['Price'] = prices
                # 7% Stop Loss logic
                active_s['Signal'] = ["🚨 SELL" if (p < float(e)*0.93 and p > 0) else "✅ OK" for p, e in zip(prices, active_s['Entry_Price'])]
                st.table(active_s)
            else:
                st.info("No active 'OPEN' trades in Swing.")
    except:
        st.warning("Please create a tab named 'SWING_PORTFOLIO' in your Google Sheet.")

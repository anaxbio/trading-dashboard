import streamlit as st
import pandas as pd
import requests
import yfinance as yf
from streamlit_gsheets import GSheetsConnection
from datetime import datetime

# --- CONFIG ---
st.set_page_config(page_title="EP Monitor", layout="wide")
st.title("🚀 EP Stage 2 Tracker")

# Ensure session state is clean
if 'scan_results' not in st.session_state:
    st.session_state.scan_results = pd.DataFrame()

conn = st.connection("gsheets", type=GSheetsConnection)

def get_live_stats(symbol):
    try:
        url = f"https://priceapi.moneycontrol.com/pricefeed/nse/equityinst/{symbol}"
        res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5).json()
        if res['msg'] == 'success':
            return {'LTP': float(res['data']['lastPrice']), 'VWAP': float(res['data']['averagePrice'])}
    except: return {'LTP': 0, 'VWAP': 0}

def run_scan(threshold):
    url = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
    try:
        tickers = pd.read_csv(url)['Symbol'].tolist()
    except: return pd.DataFrame()
    results = []
    prog = st.progress(0)
    for i, sym in enumerate(tickers[:100]):
        prog.progress(i / 100)
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
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🎯 Run 5% Scan"):
            st.session_state.scan_results = run_scan(5.0)
    with col2:
        if st.button("✅ Run 3.5% Scan"):
            st.session_state.scan_results = run_scan(3.5)

    if not st.session_state.scan_results.empty:
        st.subheader("Results")
        confirmed = []
        for i, row in st.session_state.scan_results.iterrows():
            if st.checkbox(f"Bought {row['Symbol']} @ {row['Entry']}", key=f"check_{row['Symbol']}"):
                confirmed.append({'Symbol': row['Symbol'], 'Entry': row['Entry'], 'Date': datetime.now().strftime('%Y-%m-%d'), 'Status': 'OPEN'})
        
        if confirmed:
            mode = st.radio("Target Portfolio:", ["INTRADAY_PORTFOLIO", "SWING_PORTFOLIO"])
            if st.button("💾 Save to Ledger"):
                try:
                    try:
                        old_df = conn.read(worksheet=mode, ttl=0)
                    except:
                        old_df = pd.DataFrame(columns=['Symbol', 'Entry', 'Date', 'Status'])
                    
                    new_df = pd.DataFrame(confirmed)
                    updated = pd.concat([old_df, new_df], ignore_index=True).drop_duplicates()
                    conn.update(worksheet=mode, data=updated)
                    st.success("Saved to Google Sheet!")
                    st.session_state.scan_results = pd.DataFrame()
                    st.rerun()
                except Exception as e:
                    st.error(f"Commit Failed: {e}")

with tab2:
    st.header("Intraday Monitor")
    try:
        df_i = conn.read(worksheet="INTRADAY_PORTFOLIO", ttl=0)
        if not df_i.empty:
            df_i['Status'] = df_i['Status'].astype(str).str.upper().str.strip()
            active_i = df_i[df_i['Status'] == 'OPEN'].copy()
            if not active_i.empty:
                # Manual loop is safer than .apply for testing
                ltp_list, vwap_list, sig_list = [], [], []
                for s in active_i['Symbol']:
                    stats = get_live_stats(s)
                    ltp_list.append(stats['LTP'])
                    vwap_list.append(stats['VWAP'])
                    sig_list.append("🚨 EXIT" if stats['LTP'] < stats['VWAP'] and stats['LTP'] > 0 else "✅ OK")
                
                active_i['LTP'] = ltp_list
                active_i['VWAP'] = vwap_list
                active_i['Signal'] = sig_list
                st.table(active_i[['Symbol', 'Entry', 'LTP', 'VWAP', 'Signal']])
    except: st.info("Intraday Ledger empty.")

with tab3:
    st.header("Swing Monitor")
    try:
        df_s = conn.read(worksheet="SWING_PORTFOLIO", ttl=0)
        if not df_s.empty:
            df_s['Status'] = df_s['Status'].astype(str).str.upper().str.strip()
            active_s = df_s[df_s['Status'] == 'OPEN'].copy()
            if not active_s.empty:
                ltp_s, sig_s = [], []
                for index, row in active_s.iterrows():
                    curr_ltp = get_live_stats(row['Symbol'])['LTP']
                    sl = float(row['Entry']) * 0.93
                    ltp_s.append(curr_ltp)
                    sig_s.append("🚨 SELL" if curr_ltp < sl and curr_ltp > 0 else "✅ OK")
                
                active_s['LTP'] = ltp_s
                active_s['Signal'] = sig_s
                st.table(active_s[['Symbol', 'Entry', 'LTP', 'Signal']])
    except: st.info("Swing Ledger empty.")

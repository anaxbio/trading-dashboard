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
if 'run_next_stage' not in st.session_state:
    st.session_state.run_next_stage = False

# Connect via Service Account (uses Secrets)
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

def trigger_3_5_scan():
    st.session_state.run_next_stage = True

tab1, tab2, tab3 = st.tabs(["🚀 Scanner", "💰 Intraday", "📈 Swing"])

with tab1:
    st.header("Daily Scanner")
    if st.button("🎯 Run 5% Primary Scan"):
        st.session_state.run_next_stage = False
        st.session_state.scan_results = run_scan(5.0)

    if st.session_state.scan_results.empty and not st.session_state.run_next_stage:
        st.button("✅ No 5% found. Run 3.5% Scan?", on_click=trigger_3_5_scan)

    if st.session_state.run_next_stage:
        st.session_state.scan_results = run_scan(3.5)
        st.session_state.run_next_stage = False

    if not st.session_state.scan_results.empty:
        confirmed = []
        for i, row in st.session_state.scan_results.iterrows():
            if st.checkbox(f"Bought {row['Symbol']} @ {row['Entry']}", key=f"s_{row['Symbol']}"):
                confirmed.append({'Symbol': row['Symbol'], 'Entry': row['Entry'], 'Date': datetime.now().strftime('%Y-%m-%d'), 'Status': 'OPEN'})
        
        if confirmed:
            mode = st.radio("Target Portfolio:", ["INTRADAY_PORTFOLIO", "SWING_PORTFOLIO"])
            if st.button("💾 Commit to Ledger"):
                try:
                    try:
                        old_df = conn.read(worksheet=mode, ttl=0)
                    except:
                        old_df = pd.DataFrame(columns=['Symbol', 'Entry', 'Date', 'Status'])
                    
                    updated = pd.concat([old_df, pd.DataFrame(confirmed)], ignore_index=True)
                    conn.update(worksheet=mode, data=updated)
                    st.success(f"Committed to {mode}!")
                    st.session_state.scan_results = pd.DataFrame()
                except Exception as e:
                    st.error(f"Commit failed: {e}")

with tab2:
    try:
        df_i = conn.read(worksheet="INTRADAY_PORTFOLIO", ttl=0)
        if not df_i.empty:
            df_open = df_i[df_i['Status'] == 'OPEN']
            if not df_open.empty:
                stats = [get_live_stats(s) for s in df_open['Symbol']]
                res_i = pd.concat([df_open.reset_index(drop=True), pd.DataFrame(stats)], axis=1)
                res_i['Exit?'] = res_i.apply(lambda x: "🚨 EXIT" if x['LTP'] < x['VWAP'] and x['LTP'] > 0 else "✅ OK", axis=1)
                st.table(res_i)
    except: st.info("Intraday ledger is empty.")

with tab3:
    try:
        df_s = conn.read(worksheet="SWING_PORTFOLIO", ttl=0)
        if not df_s.empty:
            df_open_s = df_s[df_s['Status'] == 'OPEN']
            if not df_open_s.empty:
                stats_s = [get_live_stats(s) for s in df_open_s['Symbol']]
                res_s = pd.concat([df_open_s.reset_index(drop=True), pd.DataFrame(stats_s)], axis=1)
                res_s['SL'] = res_s['Entry'] * 0.93
                res_s['Exit?'] = res_s.apply(lambda x: "🚨 SELL" if x['LTP'] < x['SL'] and x['LTP'] > 0 else "✅ OK", axis=1)
                st.table(res_s)
    except: st.info("Swing ledger is empty.")

import streamlit as st
import pandas as pd
import requests
import yfinance as yf
from streamlit_gsheets import GSheetsConnection
from datetime import datetime

# --- CONFIG ---
st.set_page_config(page_title="EP Monitor", layout="wide")
st.title("🚀 EP Stage 2 Tracker")

# Initialize Session State
if 'scan_results' not in st.session_state:
    st.session_state.scan_results = pd.DataFrame()
if 'scan_stage' not in st.session_state:
    st.session_state.scan_stage = "idle"

# Connect to Google Sheets
conn = st.connection("gsheets", type=GSheetsConnection)

# --- PRICE ENGINE ---
def get_live_stats(symbol):
    try:
        url = f"https://priceapi.moneycontrol.com/pricefeed/nse/equityinst/{symbol}"
        res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5).json()
        if res['msg'] == 'success':
            return {'LTP': float(res['data']['lastPrice']), 'VWAP': float(res['data']['averagePrice'])}
    except:
        pass
    return {'LTP': 0, 'VWAP': 0}

def run_scan(threshold):
    url = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
    try:
        tickers = pd.read_csv(url)['Symbol'].tolist()
    except:
        return pd.DataFrame()
    
    results = []
    prog = st.progress(0)
    for i, sym in enumerate(tickers[:120]): # Fast scan
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
                    results.append({'Symbol': sym, 'Day %': round(day_chg, 2), 'Price': round(curr_p, 2)})
        except: continue
    prog.empty()
    return pd.DataFrame(results)

# --- TABS ---
tab1, tab2, tab3 = st.tabs(["🚀 Scanner", "💰 Intraday", "📈 Swing"])

with tab1:
    st.header("Step 1: Daily Scanner")
    if st.button("🎯 Run 5% Primary Scan"):
        with st.spinner("Scanning..."):
            res = run_scan(5.0)
            if res.empty:
                st.warning("No 5% gappers found. Market might be quiet.")
                if st.button("Confirm: Run 3.5% Scan"):
                    st.session_state.scan_results = run_scan(3.5)
                    st.session_state.scan_stage = "results"
                    st.rerun()
            else:
                st.session_state.scan_results = res
                st.session_state.scan_stage = "results"
                st.rerun()

    if st.session_state.scan_stage == "results":
        st.success("Select the stocks you bought:")
        confirmed = []
        for i, row in st.session_state.scan_results.iterrows():
            if st.checkbox(f"Bought {row['Symbol']} @ {row['Price']}", key=f"s_{row['Symbol']}"):
                confirmed.append({'Symbol': row['Symbol'], 'Entry': row['Price'], 'Date': datetime.now().strftime('%Y-%m-%d'), 'Status': 'OPEN'})
        
        if confirmed:
            mode = st.radio("Target Portfolio:", ["INTRADAY_PORTFOLIO", "SWING_PORTFOLIO"])
            if st.button("💾 Save to Ledger"):
                try:
                    # Logic to read/write without crashing on empty sheets
                    try:
                        old_df = conn.read(worksheet=mode, ttl=0)
                    except:
                        old_df = pd.DataFrame(columns=['Symbol', 'Entry', 'Date', 'Status'])
                    
                    updated = pd.concat([old_df, pd.DataFrame(confirmed)], ignore_index=True)
                    conn.update(worksheet=mode, data=updated)
                    st.success(f"Committed to {mode}!")
                    st.balloons()
                except:
                    st.error("Error: Please ensure your Sheet has tabs named 'INTRADAY_PORTFOLIO' and 'SWING_PORTFOLIO'.")

with tab2:
    st.header("Live Intraday Monitor (VWAP)")
    try:
        df_i = conn.read(worksheet="INTRADAY_PORTFOLIO", ttl=0)
        if not df_i.empty:
            df_i = df_i[df_i['Status'] == 'OPEN']
            stats = [get_live_stats(s) for s in df_i['Symbol']]
            final_i = pd.concat([df_i.reset_index(drop=True), pd.DataFrame(stats)], axis=1)
            final_i['Exit?'] = final_i.apply(lambda x: "🚨 EXIT" if x['LTP'] < x['VWAP'] and x['LTP'] > 0 else "✅ OK", axis=1)
            st.table(final_i)
    except: st.info("No active trades.")

with tab3:
    st.header("Live Swing Monitor (-7% SL)")
    try:
        df_s = conn.read(worksheet="SWING_PORTFOLIO", ttl=0)
        if not df_s.empty:
            df_s = df_s[df_s['Status'] == 'OPEN']
            stats_s = [get_live_stats(s) for s in df_s['Symbol']]
            final_s = pd.concat([df_s.reset_index(drop=True), pd.DataFrame(stats_s)], axis=1)
            final_s['SL'] = final_s['Entry'] * 0.93
            final_s['Exit?'] = final_s.apply(lambda x: "🚨 SELL" if x['LTP'] < x['SL'] and x['LTP'] > 0 else "✅ OK", axis=1)
            st.table(final_s)
    except: st.info("No active trades.")

st.caption(f"Refreshed: {datetime.now().strftime('%H:%M:%S')}")

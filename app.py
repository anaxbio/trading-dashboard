import streamlit as st
import pandas as pd
import requests
import yfinance as yf
from streamlit_gsheets import GSheetsConnection
from datetime import datetime

# --- CONFIGURATION ---
st.set_page_config(page_title="EP Stage 2 Monitor", layout="wide")
st.title("🚀 EP Stage 2: Scanner & Permanent Ledger")

# Initialize Session State
if 'scan_results' not in st.session_state:
    st.session_state.scan_results = pd.DataFrame()
if 'scan_stage' not in st.session_state:
    st.session_state.scan_stage = "idle"

# Connect to Google Sheets
try:
    conn = st.connection("gsheets", type=GSheetsConnection)
except:
    st.error("Connection failed. Ensure Secrets has the correct Google Sheet URL.")

# --- DATA FETCHING ---
def get_live_stats(symbol):
    clean_sym = str(symbol).split('-')[0].strip()
    try:
        # Primary: Moneycontrol JSON
        url = f"https://priceapi.moneycontrol.com/pricefeed/nse/equityinst/{clean_sym}"
        res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5).json()
        if res['msg'] == 'success':
            return {'LTP': float(res['data']['lastPrice']), 'VWAP': float(res['data']['averagePrice'])}
    except:
        pass
    return {'LTP': 0, 'VWAP': 0}

def run_scan(threshold):
    # Fetching Nifty 500 Tickers
    url = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
    try:
        tickers = pd.read_csv(url)['Symbol'].tolist()
    except:
        return pd.DataFrame()
    
    results = []
    prog = st.progress(0)
    # Scanning top liquid batch for speed
    for i, sym in enumerate(tickers[:150]): 
        prog.progress(i / 150)
        try:
            t = yf.Ticker(f"{sym}.NS")
            hist = t.history(period="1y")
            if len(hist) > 200:
                sma200 = hist['Close'].rolling(200).mean().iloc[-1]
                prev_c = hist['Close'].iloc[-2]
                curr_p = hist['Close'].iloc[-1] 
                curr_o = hist['Open'].iloc[-1]
                
                gap = ((curr_o - prev_c) / prev_c) * 100
                day_change = ((curr_p - prev_c) / prev_c) * 100
                
                # REFINED LOGIC: Stage 2 + (Gap OR Day Running)
                if curr_p > (sma200 * 0.98) and (gap >= threshold or day_change >= threshold):
                    results.append({
                        'Symbol': sym, 
                        'Gap %': round(gap, 2), 
                        'Day %': round(day_change, 2),
                        'Price': round(curr_p, 2)
                    })
        except: continue
    prog.empty()
    return pd.DataFrame(results)

# --- DIALOGS (CONFIRMATION STEP) ---
@st.dialog("⚠️ No 5% Gappers Found")
def confirm_next_stage():
    st.write("The primary 5% scan yielded no results. This happens on quiet market days.")
    st.write("Would you like to proceed to the **Next Stage (3.5% Strength Scan)**?")
    if st.button("✅ Confirm: Run 3.5% Scan"):
        st.session_state.scan_stage = "run_3.5"
        st.rerun()
    if st.button("❌ Cancel"):
        st.session_state.scan_stage = "idle"
        st.rerun()

# --- TABS ---
tab1, tab2, tab3 = st.tabs(["🚀 Morning Scanner", "💰 Intraday Monitor", "📈 Swing Monitor"])

with tab1:
    st.header("Step 1: Daily Scanner")
    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("🎯 Start Primary 5% Scan"):
            with st.spinner("Scanning Nifty 500..."):
                res = run_scan(5.0)
                if res.empty:
                    confirm_next_stage() # Trigger the Confirmation Dialog
                else:
                    st.session_state.scan_results = res
                    st.session_state.scan_stage = "results_found"
    with col_b:
        if st.button("🔄 Reset"):
            st.session_state.scan_stage = "idle"
            st.session_state.scan_results = pd.DataFrame()
            st.rerun()

    # If user confirmed the second stage in the dialog
    if st.session_state.scan_stage == "run_3.5":
        with st.spinner("Running 3.5% Strength Scan..."):
            st.session_state.scan_results = run_scan(3.5)
            st.session_state.scan_stage = "results_found"
            st.rerun()

    if st.session_state.scan_stage == "results_found":
        st.success(f"Scan Complete! Found {len(st.session_state.scan_results)} candidates.")
        
        # Checkboxes for confirmation before commit
        confirmed_data = []
        for index, row in st.session_state.scan_results.iterrows():
            if st.checkbox(f"Bought {row['Symbol']} @ {row['Price']}", key=f"sel_{row['Symbol']}"):
                confirmed_data.append({
                    'Symbol': row['Symbol'], 
                    'Entry_Price': row['Price'], 
                    'Date': datetime.now().strftime('%Y-%m-%d'), 
                    'Status': 'OPEN'
                })
        
        if confirmed_data:
            ptype = st.radio("Add to:", ["Intraday (5x)", "Swing (Cash)"])
            if st.button("💾 Commit Selected to Ledger"):
                ws = "INTRADAY_PORTFOLIO" if "Intraday" in ptype else "SWING_PORTFOLIO"
                old_df = conn.read(worksheet=ws)
                updated_df = pd.concat([old_df, pd.DataFrame(confirmed_data)], ignore_index=True)
                conn.update(worksheet=ws, data=updated_df)
                st.success(f"Committed to {ws}!")
                st.balloons()

with tab2:
    st.header("Intraday Live VWAP (5x)")
    try:
        df_i = conn.read(worksheet="INTRADAY_PORTFOLIO")
        df_i = df_i[df_i['Status'] == 'OPEN']
        if not df_i.empty:
            stats = [get_live_stats(s) for s in df_i['Symbol']]
            final_i = pd.concat([df_i.reset_index(drop=True), pd.DataFrame(stats)], axis=1)
            final_i['Signal'] = final_i.apply(lambda x: "🚨 EXIT" if x['LTP'] < x['VWAP'] and x['LTP'] != 0 else "✅ HOLD", axis=1)
            st.table(final_i[['Symbol', 'Entry_Price', 'LTP', 'VWAP', 'Signal']])
    except: st.info("No active Intraday trades.")

with tab3:
    st.header("Swing Monitor (Cash)")
    try:
        df_s = conn.read(worksheet="SWING_PORTFOLIO")
        df_s = df_s[df_s['Status'] == 'OPEN']
        if not df_s.empty:
            stats_s = [get_live_stats(s) for s in df_s['Symbol']]
            final_s = pd.concat([df_s.reset_index(drop=True), pd.DataFrame(stats_s)], axis=1)
            final_s['SL'] = final_s['Entry_Price'] * 0.93
            final_s['Action'] = final_s.apply(lambda x: "🚨 SELL" if x['LTP'] < x['SL'] and x['LTP'] != 0 else "✅ OK", axis=1)
            st.table(final_s[['Symbol', 'Entry_Price', 'SL', 'LTP', 'Action']])
    except: st.info("No active Swing trades.")

st.caption(f"Refreshed: {datetime.now().strftime('%H:%M:%S')}")

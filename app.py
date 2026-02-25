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
        url = f"https://priceapi.moneycontrol.com/pricefeed/nse/equityinst/{clean_sym}"
        res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5).json()
        if res['msg'] == 'success':
            return {'LTP': float(res['data']['lastPrice']), 'VWAP': float(res['data']['averagePrice'])}
    except:
        pass
    return {'LTP': 0, 'VWAP': 0}

def run_scan(gap_threshold):
    url = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
    try:
        tickers = pd.read_csv(url)['Symbol'].tolist()
    except:
        return pd.DataFrame()
    results = []
    prog = st.progress(0)
    for i, sym in enumerate(tickers[:150]): 
        prog.progress(i / 150)
        try:
            t = yf.Ticker(f"{sym}.NS")
            hist = t.history(period="1y")
            if len(hist) > 200:
                sma200 = hist['Close'].rolling(200).mean().iloc[-1]
                prev_c = hist['Close'].iloc[-2]
                curr_o = hist['Open'].iloc[-1]
                gap = ((curr_o - prev_c) / prev_c) * 100
                if curr_o > sma200 and gap >= gap_threshold:
                    results.append({'Symbol': sym, 'Gap %': round(gap, 2), 'Price': round(curr_o, 2)})
        except: continue
    prog.empty()
    return pd.DataFrame(results)

# --- TABS ---
tab1, tab2, tab3 = st.tabs(["🚀 Morning Scanner", "💰 Intraday Monitor", "📈 Swing Monitor"])

with tab1:
    st.header("1. Find & Confirm Purchases")
    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("🎯 Start Primary 5% Scan"):
            res = run_scan(5.0)
            if res.empty: st.session_state.scan_stage = "first_failed"
            else:
                st.session_state.scan_results = res
                st.session_state.scan_stage = "results_found"
    with col_b:
        if st.button("🔄 Reset"):
            st.session_state.scan_stage = "idle"
            st.rerun()

    if st.session_state.scan_stage == "first_failed":
        st.warning("No 5% gappers. Try 3.5%?")
        if st.button("✅ Yes, Scan 3.5%"):
            st.session_state.scan_results = run_scan(3.5)
            st.session_state.scan_stage = "results_found"
            st.rerun()

    if st.session_state.scan_stage == "results_found":
        st.info("Check boxes for stocks actually bought in Stoxkart:")
        confirmed_data = []
        for index, row in st.session_state.scan_results.iterrows():
            if st.checkbox(f"Bought {row['Symbol']} @ {row['Price']}", key=f"cb_{row['Symbol']}"):
                confirmed_data.append({'Symbol': row['Symbol'], 'Entry_Price': row['Price'], 'Date': datetime.now().strftime('%Y-%m-%d'), 'Status': 'OPEN'})
        
        if confirmed_data:
            portfolio_type = st.radio("Save to which Portfolio?", ["Intraday (5x)", "Swing (Cash)"])
            if st.button("💾 Commit to Google Sheet Ledger"):
                sheet_name = "INTRADAY_PORTFOLIO" if "Intraday" in portfolio_type else "SWING_PORTFOLIO"
                
                # Fetch existing data to append
                existing_df = conn.read(worksheet=sheet_name)
                new_df = pd.concat([existing_df, pd.DataFrame(confirmed_data)], ignore_index=True)
                
                # Update the Google Sheet
                conn.update(worksheet=sheet_name, data=new_df)
                st.success(f"Saved to {sheet_name}! History is now permanent.")
                st.balloons()

with tab2:
    st.header("Intraday Live VWAP (5x)")
    # Logic to show what's in the Google Sheet INTRADAY_PORTFOLIO
    try:
        df_i = conn.read(worksheet="INTRADAY_PORTFOLIO")
        df_i = df_i[df_i['Status'] == 'OPEN']
        if not df_i.empty:
            stats = [get_live_stats(s) for s in df_i['Symbol']]
            final_i = pd.concat([df_i.reset_index(drop=True), pd.DataFrame(stats)], axis=1)
            final_i['Signal'] = final_i.apply(lambda x: "🚨 EXIT" if x['LTP'] < x['VWAP'] and x['LTP'] != 0 else "✅ HOLD", axis=1)
            st.table(final_i[['Symbol', 'Entry_Price', 'LTP', 'VWAP', 'Signal']])
    except: st.info("No open Intraday trades in Ledger.")

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
    except: st.info("No open Swing trades in Ledger.")

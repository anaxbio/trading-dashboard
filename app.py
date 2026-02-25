import streamlit as st
import pandas as pd
import requests
import yfinance as yf
from streamlit_gsheets import GSheetsConnection
from datetime import datetime

# --- CONFIG ---
st.set_page_config(page_title="EP Monitor", layout="wide")
st.title("🚀 EP Stage 2 Tracker")

# Session State for Scan results
if 'scan_results' not in st.session_state:
    st.session_state.scan_results = pd.DataFrame()
if 'run_next_stage' not in st.session_state:
    st.session_state.run_next_stage = False

# Connection
conn = st.connection("gsheets", type=GSheetsConnection)

def get_live_stats(symbol):
    try:
        url = f"https://priceapi.moneycontrol.com/pricefeed/nse/equityinst/{symbol}"
        res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5).json()
        if res['msg'] == 'success':
            return {'LTP': float(res['data']['lastPrice']), 'VWAP': float(res['data']['averagePrice'])}
    except: return {'LTP': 0, 'VWAP': 0}

# --- TABS ---
tab1, tab2, tab3 = st.tabs(["🚀 Scanner", "💰 Intraday", "📈 Swing"])

with tab1:
    st.header("Daily Scanner")
    # ... (Keep your existing run_scan and scan logic here) ...
    # [Assuming scan logic remains unchanged from previous step]
    
    # --- FIXED COMMIT LOGIC ---
    if 'confirmed_data' in st.session_state and st.session_state.confirmed_data:
        mode = st.radio("Target Portfolio:", ["INTRADAY_PORTFOLIO", "SWING_PORTFOLIO"])
        if st.button("💾 Commit to Ledger"):
            try:
                # 1. Force read fresh data
                try:
                    old_df = conn.read(worksheet=mode, ttl=0)
                except:
                    old_df = pd.DataFrame(columns=['Symbol', 'Entry', 'Date', 'Status'])
                
                # 2. Append and Update
                new_entries = pd.DataFrame(st.session_state.confirmed_data)
                updated = pd.concat([old_df, new_entries], ignore_index=True)
                conn.update(worksheet=mode, data=updated)
                
                st.success(f"Successfully saved to {mode}!")
                st.session_state.scan_results = pd.DataFrame() # Clear scan
                st.session_state.confirmed_data = [] # Clear selection
                st.cache_data.clear() # CLEAR CACHE to force Tab 2/3 to see new data
                st.rerun() 
            except Exception as e:
                st.error(f"Error during commit: {e}")

with tab2:
    st.header("Intraday Monitor")
    if st.button("🔄 Refresh Data"):
        st.cache_data.clear()
        st.rerun()

    try:
        # ttl=0 is vital here
        df_i = conn.read(worksheet="INTRADAY_PORTFOLIO", ttl=0)
        
        if not df_i.empty:
            # CLEAN DATA: Remove spaces and make uppercase
            df_i['Status'] = df_i['Status'].astype(str).str.strip().str.upper()
            
            # Filter for OPEN positions
            active_i = df_i[df_i['Status'] == 'OPEN'].copy()
            
            if not active_i.empty:
                # Get stats for active ones
                stats = [get_live_stats(s) for s in active_i['Symbol']]
                res_i = pd.concat([active_i.reset_index(drop=True), pd.DataFrame(stats)], axis=1)
                res_i['Exit?'] = res_i.apply(lambda x: "🚨 EXIT" if x['LTP'] < x['VWAP'] and x['LTP'] > 0 else "✅ OK", axis=1)
                st.dataframe(res_i, use_container_width=True)
            else:
                st.warning("No rows with status 'OPEN' found in Intraday tab.")
        else:
            st.info("Intraday Sheet is empty.")
    except Exception as e:
        st.error(f"Tab 2 Error: {e}")

with tab3:
    st.header("Swing Monitor")
    if st.button("🔄 Refresh Swing Data"):
        st.cache_data.clear()
        st.rerun()

    try:
        df_s = conn.read(worksheet="SWING_PORTFOLIO", ttl=0)
        if not df_s.empty:
            df_s['Status'] = df_s['Status'].astype(str).str.strip().str.upper()
            active_s = df_s[df_s['Status'] == 'OPEN'].copy()
            
            if not active_s.empty:
                stats_s = [get_live_stats(s) for s in active_s['Symbol']]
                res_s = pd.concat([active_s.reset_index(drop=True), pd.DataFrame(stats_s)], axis=1)
                res_s['SL'] = res_s['Entry'] * 0.93
                res_s['Exit?'] = res_s.apply(lambda x: "🚨 SELL" if x['LTP'] < x['SL'] and x['LTP'] > 0 else "✅ OK", axis=1)
                st.dataframe(res_s, use_container_width=True)
            else:
                st.warning("No rows with status 'OPEN' found in Swing tab.")
    except Exception as e:
        st.error(f"Tab 3 Error: {e}")

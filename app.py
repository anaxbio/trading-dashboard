# app.py
import streamlit as st
import pandas as pd
from streamlit_gsheets import GSheetsConnection
import time
import numpy as np

# --- IMPORT THE BRAIN ---
from trading_engine import (
    get_now_ist, is_market_open, get_vwap_data, get_swing_stops, 
    run_engine, fetch_etf_universe, categorize_etf, calc_silent_signal
)

st.set_page_config(page_title="EP Dual-Engine Cockpit", layout="wide")
st.title("🛡️ EP Strategy: Multi-Asset War Room")
conn = st.connection("gsheets", type=GSheetsConnection)

tab1, tab2, tab3, tab4 = st.tabs(["🚀 INTRADAY 5X", "📈 STAGE 2 SWING", "🛡️ ETF ALIGNER", "🔭 SILENT SIGNAL"])

# ==========================================
# TAB 1: INTRADAY 5X
# ==========================================
with tab1:
    st.subheader("Step 1: Intraday Hunter")
    col_cap, col_info = st.columns([2, 1])
    with col_cap: intra_capital = st.slider("Total Buying Power (₹)", 10000, 1000000, 100000, 10000)
    with col_info: st.metric("Required Cash Margin", f"₹{int(intra_capital / 5):,}")
    
    if st.button("🔥 Scan Intraday Movers"): 
        st.session_state.intra_results = run_engine(4.0, use_sma_wall=False)
        
    if 'intra_results' in st.session_state and not st.session_state.intra_results.empty:
        df_i = st.session_state.intra_results.copy()
        df_i['Qty'] = (intra_capital / df_i['LTP']).astype(int)
        df_i['Max_Loss (₹)'] = ((df_i['LTP'] - df_i['Sys_SL']) * df_i['Qty']).round(2)
        st.dataframe(df_i[['Rank', 'Symbol', 'LTP', 'Max_Loss (₹)', 'Sys_SL', 'Qty']], hide_index=True)
        
        with st.form("intra_commit"):
            confirmed = []
            for _, r in df_i.iterrows():
                if r['Rank'] == "🔥 LEADER":
                    if st.checkbox(f"Trade {r['Symbol']} (Risk: ₹{r['Max_Loss (₹)']})", key=f"intra_{r['Symbol']}"):
                        confirmed.append({'Symbol': r['Symbol'], 'Entry_Price': r['LTP'], 'Qty': r['Qty'], 'Date': get_now_ist().strftime('%Y-%m-%d %H:%M'), 'Status': 'OPEN'})
            if st.form_submit_button("💾 COMMIT TO WAR ROOM"):
                df_cur = conn.read(worksheet="INTRADAY_PORTFOLIO", ttl=0).dropna(how='all')
                conn.update(worksheet="INTRADAY_PORTFOLIO", data=pd.concat([df_cur, pd.DataFrame(confirmed)], ignore_index=True))
                st.success("Committed!"); time.sleep(1); st.rerun()

    st.write("---")
    st.subheader("🛰️ Active War Room")
    @st.fragment(run_every="120s")
    def live_intra():
        if not is_market_open(): st.info("😴 Market Closed.")
        try:
            df = conn.read(worksheet="INTRADAY_PORTFOLIO", ttl=0).dropna(how='all')
            active = df[df['Status'].astype(str).str.upper() == 'OPEN'].copy()
            if active.empty: return st.write("No active trades.")
            
            # ... (Keep your existing Trade Management and Live P&L logic here) ...
            
        except Exception as e: st.error(f"Error: {e}")
    live_intra()

# ... (Keep your Tab 2, Tab 3, and Tab 4 UI code exactly the same as before) ...

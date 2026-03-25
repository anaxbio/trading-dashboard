import streamlit as st
import pandas as pd
from streamlit_gsheets import GSheetsConnection
import time
import numpy as np
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor

# --- IMPORT THE BRAIN ---
from trading_engine import (
    get_now_ist, is_market_open, get_vwap_data, get_swing_stops, 
    run_engine, fetch_etf_universe, categorize_etf, calc_silent_signal
)

# --- CONFIG & SETUP ---
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
    with col_cap:
        intra_capital = st.slider("Total Buying Power (₹) [Incl. 5X Leverage]", 10000, 1000000, 100000, 10000)
    with col_info:
        st.metric("Required Cash Margin", f"₹{int(intra_capital / 5):,}")

    if st.button("🔥 Scan Intraday Movers"):
        st.session_state.intra_results = run_engine(4.0, use_sma_wall=False)
    
    if 'intra_results' in st.session_state:
        if not st.session_state.intra_results.empty:
            df_i = st.session_state.intra_results.copy()
            df_i['Qty'] = (intra_capital / df_i['LTP']).astype(int)
            df_i['Max_Loss (₹)'] = ((df_i['LTP'] - df_i['Sys_SL']) * df_i['Qty']).round(2)
            
            st.dataframe(df_i[['Rank', 'Symbol', 'LTP', 'Max_Loss (₹)', 'Sys_SL', 'Qty']], hide_index=True)
            
            with st.form("intra_commit"):
                confirmed = []
                for _, r in df_i.iterrows():
                    if r['Rank'] == "🔥 LEADER":
                        if st.checkbox(f"Trade {r['Symbol']} (Qty: {r['Qty']} | Risk: ₹{r['Max_Loss (₹)']})", key=f"intra_{r['Symbol']}"):
                            confirmed.append({
                                'Symbol': r['Symbol'], 'Entry_Price': r['LTP'], 'Qty': r['Qty'], 
                                'Date': get_now_ist().strftime('%Y-%m-%d %H:%M'), 'Status': 'OPEN'
                            })
                if st.form_submit_button("💾 COMMIT TO WAR ROOM"):
                    df_cur = conn.read(worksheet="INTRADAY_PORTFOLIO", ttl=0).dropna(how='all')
                    conn.update(worksheet="INTRADAY_PORTFOLIO", data=pd.concat([df_cur, pd.DataFrame(confirmed)], ignore_index=True))
                    st.success("Committed!"); time.sleep(1); st.rerun()
        else:
            st.warning("🚨 0 stocks passed the VWAP Risk Filter.")

    st.write("---")
    st.subheader("🛰️ Active War Room (Ratchet SL Active)")
    
    @st.fragment(run_every="120s")
    def live_intra():
        if not is_market_open(): st.info("😴 Market Closed. Live feeds paused.")
        try:
            df = conn.read(worksheet="INTRADAY_PORTFOLIO", ttl=0).dropna(how='all')
            active = df[df['Status'].astype(str).str.upper() == 'OPEN'].copy()
            
            if active.empty: return st.write("No active trades.")

            with st.expander("📝 Manage Trades & Record Exits"):
                with st.form("edit_intra_positions"):
                    updated_rows = []
                    c1, c2, c3, c4, c5 = st.columns([1.5, 1, 1.2, 1.2, 1.5])
                    c1.caption("Symbol"); c2.caption("Qty"); c3.caption("Buy Price"); c4.caption("Exit Price"); c5.caption("Action")
                    
                    for idx, r in active.iterrows():
                        c1, c2, c3, c4, c5 = st.columns([1.5, 1, 1.2, 1.2, 1.5])
                        c1.markdown(f"**{r['Symbol']}**")
                        curr_qty = int(float(r['Qty'])) if 'Qty' in r and pd.notna(r['Qty']) else 0
                        
                        new_q = c2.number_input("Qty", value=curr_qty, step=1, key=f"iq_{idx}", label_visibility="collapsed")
                        new_p = c3.number_input("Buy", value=float(r['Entry_Price']), step=0.05, key=f"ip_{idx}", label_visibility="collapsed")
                        exit_p = c4.number_input("Exit", value=0.00, step=0.05, key=f"ep_{idx}", label_visibility="collapsed")
                        new_s = c5.selectbox("Action", ["HOLD", "CLOSE TRADE"], index=0, key=f"ist_{idx}", label_visibility="collapsed")
                        
                        updated_rows.append({'idx': idx, 'q': new_q, 'p': new_p, 'ep': exit_p, 's': new_s})
                    
                    if st.form_submit_button("✅ Update / Close Trades"):
                        for u in updated_rows:
                            df.at[u['idx'], 'Qty'] = u['q']
                            df.at[u['idx'], 'Entry_Price'] = u['p']
                            if u['s'] == "CLOSE TRADE":
                                df.at[u['idx'], 'Status'] = "EXIT"
                                df.at[u['idx'], 'Exit_Price'] = u['ep']
                        conn.update(worksheet="INTRADAY_PORTFOLIO", data=df)
                        st.rerun()

            rows = []
            total_session_pnl = 0.0

            for _, r in active.iterrows():
                ltp, vwap, hod = get_vwap_data(r['Symbol'])
                entry = float(r['Entry_Price'])
                qty = int(float(r['Qty'])) if 'Qty' in r and pd.notna(r['Qty']) else 0
                rupee_pnl = round((ltp - entry) * qty, 2)
                total_session_pnl += rupee_pnl
                
                base_sl = round(vwap - 2.0, 2)
                if hod >= (entry * 1.01):
                    trail_sl = round(hod * 0.99, 2) 
                    sys_sl = max(base_sl, entry, trail_sl)
                    sl_type = "🔒 RATCHET"
                else:
                    sys_sl = base_sl
                    sl_type = "🛡️ VWAP-2"
                
                pnl_display = f"🟢 ₹{rupee_pnl:,.2f}" if rupee_pnl >= 0 else f"🔴 -₹{abs(rupee_pnl):,.2f}"
                rows.append({
                    "Symbol": r['Symbol'], "Qty": qty, "Entry": entry, "LTP": ltp, 
                    f"Active SL ({sl_type})": sys_sl, "Live P&L": pnl_display, 
                    "Signal": "✅ HOLD" if ltp > sys_sl else "🚨 EXIT NOW"
                })
            
            st.metric("Total Session P&L", f"₹{round(total_session_pnl, 2):,}")
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
            
            for r in rows:
                if "EXIT NOW" in r['Signal']: st.error(f"🚨 {r['Symbol']} has broken its Trailing SL!")
                    
        except Exception as e:
            st.error(f"War Room Sync Error: {e}")

    live_intra()

# ==========================================
# TAB 2: STAGE 2 SWING
# ==========================================
with tab2:
    st.subheader("Step 1: Swing Engine")
    
    col_u, col_b = st.columns([2, 1])
    with col_u: choice = st.radio("Target Universe:", ["Nifty 500", "Microcap 250"], horizontal=True)
    with col_b: swing_alloc = st.number_input("Budget Per Stock (₹)", 5000, 500000, 20000, 5000)
    
    if st.button(f"🚀 Scan {choice} Leaders"):
        st.session_state.swing_results = run_engine(5.0, use_sma_wall=True, universe=choice)
    
    if 'swing_results' in st.session_state and not st.session_state.swing_results.empty:

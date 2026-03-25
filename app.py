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
        df_s = st.session_state.swing_results.copy()
        df_s['Qty'] = (swing_alloc / df_s['LTP']).astype(int)
        
        st.dataframe(df_s[['Rank', 'Symbol', 'LTP', 'Dist_Wall%', 'Qty']], hide_index=True)
        
        with st.form("swing_commit"):
            confirmed_s = []
            for _, r in df_s.iterrows():
                if r['Rank'] == "🔥 LEADER":
                    if st.checkbox(f"Allocate ₹{swing_alloc:,} to {r['Symbol']} (Qty: {r['Qty']})", key=f"sw_{r['Symbol']}"):
                        confirmed_s.append({
                            'Symbol': r['Symbol'], 'Entry_Price': r['LTP'], 'Qty': r['Qty'], 
                            'Date': get_now_ist().strftime('%Y-%m-%d'), 'Status': 'OPEN'
                        })
            if st.form_submit_button("💾 COMMIT SWING"):
                df_cur_s = conn.read(worksheet="SWING_PORTFOLIO", ttl=0).dropna(how='all')
                conn.update(worksheet="SWING_PORTFOLIO", data=pd.concat([df_cur_s, pd.DataFrame(confirmed_s)], ignore_index=True))
                st.success("Committed!"); time.sleep(1); st.rerun()

    st.write("---")
    st.subheader("🛡️ Active Swing Risk Guard")
    
    try:
        df_sw = conn.read(worksheet="SWING_PORTFOLIO", ttl=0).dropna(how='all')
        active_sw = df_sw[df_sw['Status'].astype(str).str.upper() == 'OPEN'].copy()
        
        if not active_sw.empty:
            with st.expander("📝 Edit Swing Prices, Qty & Status"):
                with st.form("edit_swing_positions"):
                    sw_upd = []
                    c1, c2, c3, c4 = st.columns([1.5, 1, 1.5, 1])
                    c1.caption("Symbol"); c2.caption("Qty"); c3.caption("Buy Price"); c4.caption("Status")
                    
                    for idx, r in active_sw.iterrows():
                        c1, c2, c3, c4 = st.columns([1.5, 1, 1.5, 1])
                        c1.markdown(f"**{r['Symbol']}**")
                        curr_qty = int(float(r['Qty'])) if 'Qty' in r and pd.notna(r['Qty']) else 0
                        
                        new_q = c2.number_input("Qty", value=curr_qty, step=1, key=f"sq_{idx}", label_visibility="collapsed")
                        new_p = c3.number_input("Price", value=float(r['Entry_Price']), step=0.05, key=f"sp_{idx}", label_visibility="collapsed")
                        new_s = c4.selectbox("Status", ["OPEN", "EXIT"], index=0, key=f"sst_{idx}", label_visibility="collapsed")
                        sw_upd.append({'idx': idx, 'q': new_q, 'p': new_p, 's': new_s})
                    
                    if st.form_submit_button("✅ Update Swing Ledger"):
                        for u in sw_upd:
                            df_sw.at[u['idx'], 'Qty'] = u['q']
                            df_sw.at[u['idx'], 'Entry_Price'] = u['p']
                            df_sw.at[u['idx'], 'Status'] = u['s']
                        conn.update(worksheet="SWING_PORTFOLIO", data=df_sw)
                        st.rerun()

            sw_rows = []
            for idx, r in active_sw.iterrows():
                hard, trail, ltp = get_swing_stops(r['Symbol'])
                entry = float(r['Entry_Price'])
                qty = int(float(r['Qty'])) if 'Qty' in r and pd.notna(r['Qty']) else 0
                
                rupee_pnl = round((ltp - entry) * qty, 2)
                pnl_display = f"🟢 ₹{rupee_pnl:,.2f}" if rupee_pnl >= 0 else f"🔴 -₹{abs(rupee_pnl):,.2f}"
                
                sw_rows.append({
                    "Symbol": r['Symbol'], "Entry": entry, "Qty": qty, "LTP": ltp, 
                    "P&L": pnl_display, "HARD SL": hard, "TRAIL SL": trail
                })
            
            st.dataframe(pd.DataFrame(sw_rows), hide_index=True, use_container_width=True)
        else:
            st.info("Swing portfolio empty.")
    except Exception as e:
        st.error(f"Sync Error: {e}")

# ==========================================
# TAB 3: TACTICAL ETF ALIGNER
# ==========================================
with tab3:
    st.subheader("🛡️ Tactical ETF Momentum & Inverse Volatility Aligner")
    etf_universe = fetch_etf_universe()
    
    with st.expander(f"🔍 View the Dynamically Fetched Universe ({len(etf_universe)} Candidates)"):
        st.write(", ".join(sorted(etf_universe)))
    
    col_cash, col_scan = st.columns([1, 1])
    with col_cash:
        fresh_cash = st.number_input("Fresh Cash to Deploy (₹)", value=10000, step=1000)
    with col_scan:
        st.write("") 
        if st.button("🔄 Run Momentum & 63-Day Volatility Scan"):
            st.session_state.run_etf_scan = True 
            st.session_state.pop("etf_top_6", None)
    
    st.write("---")
    st.markdown("#### 1. JSON Portfolio Inventory")
    
    if "json_holdings" not in st.session_state:
        st.session_state.json_holdings = pd.DataFrame([
            {"Symbol": "GOLDCASE", "Locked_Units": 3560, "Avg_Price": 26.42},
            {"Symbol": "PSUBNKBEES", "Locked_Units": 653, "Avg_Price": 104.14},
            {"Symbol": "METALIETF", "Locked_Units": 3585, "Avg_Price": 11.95},
            {"Symbol": "SILVERIETF", "Locked_Units": 126, "Avg_Price": 287.10}
        ])

    edited_holdings = st.data_editor(st.session_state.json_holdings, num_rows="dynamic", use_container_width=True)
    st.session_state.json_holdings = edited_holdings 
    
    live_portfolio = []
    total_holdings_val = 0.0
    total_unrealized_pnl = 0.0

    for _, r in edited_holdings.iterrows():
        sym = str(r['Symbol']).strip().upper()
        units = int(r.get('Locked_Units', 0))
        avg_p = float(r.get('Avg_Price', 0.0))

        if sym and units > 0:
            ltp, _, _ = get_vwap_data(sym)
            if ltp == 0.0: ltp = avg_p 
            live_val = units * ltp
            pnl = (ltp - avg_p) * units
            total_holdings_val += live_val
            total_unrealized_pnl += pnl

            pnl_display = f"🟢 ₹{pnl:,.2f}" if pnl >= 0 else f"🔴 -₹{abs(pnl):,.2f}"
            live_portfolio.append({
                "Symbol": sym, "Units": units, "Avg Price": avg_p, 
                "LTP": ltp, "Live Value (₹)": round(live_val, 2), "Live P&L": pnl_display
            })

    st.markdown("#### 2. Live Holdings Tracker")
    if live_portfolio:
        st.dataframe(pd.DataFrame(live_portfolio), hide_index=True, use_container_width=True)

    total_portfolio_val = total_holdings_val + fresh_cash
    c1, c2 = st.columns(2)
    c1.metric("Total Buying Power (Holdings + Cash)", f"₹{total_portfolio_val:,.2f}")
    c2.metric("Total Unrealized P&L", f"₹{total_unrealized_pnl:,.2f}", delta=f"{round(total_unrealized_pnl, 2)}")

    st.write("---")
    
    if st.session_state.get('run_etf_scan', False):
        if "etf_top_6" not in st.session_state:
            prog_etf = st.progress(0, text="Fetching Live ETF Market Data...")
            etf_results = []
            
            def calc_63d_vol(sym):
                try:
                    t = yf.Ticker(f"{sym}.NS")
                    hist = t.history(period="1y")
                    if len(hist) < 60: return None
                    
                    p_curr = hist['Close'].iloc[-1]
                    def safe_ret(days):
                        return (p_curr - hist['Close'].iloc[-days]) / hist['Close'].iloc[-days] if len(hist) >= days else 0.0

                    score = (safe_ret(63)*0.25) + (safe_ret(126)*0.25) + (safe_ret(189)*0.25) + (safe_ret(252)*0.25)
                    if score > 1.0 or score < -0.9: return None 
                    
                    daily_rets = hist['Close'].pct_change().dropna()
                    vol_63d = daily_rets.tail(63).std() * np.sqrt(252)
                    
                    if vol_63d == 0 or np.isnan(vol_63d): return None
                    
                    cat = categorize_etf(sym)
                    return {'Category': cat, 'Symbol': sym, 'LTP': round(p_curr, 2), 'Momentum_Score': score, 'Vol_63D': vol_63d, 'Inv_Vol': 1 / vol_63d}
                except: return None
            
            with ThreadPoolExecutor(max_workers=25) as executor:
                futures = [executor.submit(calc_63d_vol, s) for s in etf_universe]
                for i, f in enumerate(futures):
                    prog_etf.progress((i+1)/len(etf_universe), text=f"Analyzing {etf_universe[i]}... ({i+1}/{len(etf_universe)})")
                    res = f.result()
                    if res: etf_results.append(res)
            
            prog_etf.empty()
            
            if etf_results:
                df_etf = pd.DataFrame(etf_results)
                owned_symbols = [x['Symbol'] for x in live_portfolio if x['Units'] > 0]
                df_etf['Owned'] = df_etf['Symbol'].isin(owned_symbols)
                df_etf = df_etf.sort_values(by=['Category', 'Owned', 'Momentum_Score'], ascending=[True, False, False])
                df_dedup = df_etf.drop_duplicates(subset=['Category'], keep='first').copy()
                df_dedup = df_dedup.sort_values(by='Momentum_Score', ascending=False).reset_index(drop=True)
                
                top_6 = df_dedup.head(6).copy()
                core_4 = top_6.head(4).copy()
                sum_inv_vol = core_4['Inv_Vol'].sum()
                
                top_6['Target_Weight_%'] = 0.0
                for i in range(len(core_4)):
                    top_6.loc[i, 'Target_Weight_%'] = (top_6.loc[i, 'Inv_Vol'] / sum_inv_vol) * 100
                
                top_6['Role'] = ["Core"]*len(core_4) + ["Bench"]*(len(top_6) - len(core_4))
                st.session_state.etf_top_6 = top_6
        
        if "etf_top_6" in st.session_state:
            top_6 = st.session_state.etf_top_6.copy()
            core_4 = top_6[top_6['Role'] == 'Core'].copy()
            
            st.markdown("#### 3. Momentum Leaderboard")
            st.dataframe(
                top_6[['Role', 'Category', 'Symbol', 'LTP', 'Momentum_Score', 'Vol_63D', 'Target_Weight_%']], 
                hide_index=True, use_container_width=True
            )
            
            st.markdown("#### 4. Execution Terminal")
            exec_rows = []
            core_symbols = core_4['Symbol'].tolist()
            owned_symbols = [x['Symbol'] for x in live_portfolio if x['Units'] > 0]
            all_exec_symbols = list(set(core_symbols + owned_symbols))
            
            for sym in all_exec_symbols:
                current_val = 0; ltp = 0
                owned_item = next((item for item in live_portfolio if item['Symbol'] == sym), None)
                if owned_item:
                    current_val = owned_item['Live Value (₹)']
                    ltp = owned_item['LTP']
                else:
                    ltp = float(top_6.loc[top_6['Symbol'] == sym, 'LTP'].values[0])
                    
                ideal_capital = 0.0
                if sym in core_symbols:
                    target_weight = float(top_6.loc[top_6['Symbol'] == sym, 'Target_Weight_%'].values[0]) / 100
                    ideal_capital = total_portfolio_val * target_weight
                
                capital_gap = ideal_capital - current_val
                units_to_transact = int(capital_gap / ltp) if ltp > 0 else 0
                action = "BUY (New Leader)" if units_to_transact > 0 else "SELL (Rebalance)"
                if abs(units_to_transact) < 1: action = "HOLD"
                
                if sym not in core_symbols and current_val > 0:
                    action = "SELL ALL (Drop from Core)"
                    units_to_transact = -int(current_val / ltp)
                    capital_gap = -current_val
                
                if action != "HOLD" or current_val > 0:
                    cat = categorize_etf(sym)
                    exec_rows.append({
                        "Symbol": sym, "Category": cat, "Target Allocation": f"₹{ideal_capital:,.2f}",
                        "Current Value": f"₹{current_val:,.2f}", "Action": action,
                        "Units": abs(units_to_transact), "Capital Required / Freed": f"₹{capital_gap:,.2f}"
                    })
            
            if exec_rows:
                df_exec = pd.DataFrame(exec_rows)
                st.dataframe(df_exec, hide_index=True, use_container_width=True)
            else:
                st.info("Portfolio perfectly aligned.")

# ==========================================
# TAB 4: SILENT SIGNAL (Regime Tracker)
# ==========================================
with tab4:
    st.subheader("🔭 SilentSignal - Trend Regime Tracker & War Room")
    
    col_w, col_c = st.columns([2, 1])
    with col_w:
        if "ss_watchlist" not in st.session_state:
            st.session_state.ss_watchlist = pd.DataFrame({
                "Yahoo Ticker": ["^NSEI", "^NSEBANK", "GOLDM26APR2026.MX", "SILVERMIC.MX"]
            })
        
        st.caption("📝 Edit Watchlist: Click an empty row to add, or select a row and press Delete to remove.")
        edited_watchlist = st.data_editor(
            st.session_state.ss_watchlist, 
            num_rows="dynamic", 
            use_container_width=True, 
            hide_index=True
        )
        st.session_state.ss_watchlist = edited_watchlist
        raw_watchlist = edited_watchlist["Yahoo Ticker"].dropna().astype(str).tolist()
        watchlist = [x.strip().upper() for x in raw_watchlist if x.strip()]

    with col_c:
        ss_capital = st.number_input("Capital Per Trade (₹)", value=100000, step=10000)
        tf = st.selectbox("Regime Timeframe", ["15m", "1h", "1d"], index=2)
    
    period_map = {"15m": "60d", "1h": "60d", "1d": "1y"}

    if st.button("🛰️ Scan Silent Signal"):
        ss_results = []
        failed_tickers = []
        
        prog_ss = st.progress(0, text="Calculating Pivot Trends...")
        with ThreadPoolExecutor(max_workers=5) as ex:
            futures = {ex.submit(calc_silent_signal, s, tf, period_map[tf]): s for s in watchlist}
            for i, f in enumerate(futures):
                ticker_name = futures[f]
                prog_ss.progress((i+1)/len(watchlist))
                res = f.result()
                if res: 
                    ss_results.append(res)
                else:
                    failed_tickers.append(ticker_name)
        prog_ss.empty()
        
        # --- THE MISSING ASSET CATCHER ---
        for failed in failed_tickers:
            ss_results.append({
                "Symbol": failed, "LTP": 0.0, "Trend": "UNKNOWN", "StopLoss": 0.0, 
                "ADX": 0.0, "Regime": "ERROR", "Signal": "⚠️ DATA ERROR (<200 bars)"
            })
            
        if ss_results:
            df_ss = pd.DataFrame(ss_results)
            
            # Risk Math & Explicit Status
            df_ss['Qty'] = np.where(df_ss['LTP'] > 0, (ss_capital / df_ss['LTP']).astype(int), 0)
            df_ss['Risk/Share'] = abs(df_ss['LTP'] - df_ss['StopLoss']).round(2)
            df_ss['Risk %'] = np.where(df_ss['LTP'] > 0, ((df_ss['Risk/Share'] / df_ss['LTP']) * 100).round(2), 0.0)
            df_ss['Total Risk (₹)'] = (df_ss['Risk/Share'] * df_ss['Qty']).round(2)
            
            def get_action_status(row):
                if "ERROR" in row['Signal']: return row['Signal']
                if row['Regime'] == "CHOP": return "🚫 NO TRADE (ADX < 20)"
                if "NEW BUY" in row['Signal']: return "🟢 NEW LONG"
                if "NEW SELL" in row['Signal']: return "🔴 NEW SHORT"
                if "HOLDING" in row['Signal']: 
                    return "⏳ HOLD LONG" if "BULL" in row['Trend'] else "⏳ HOLD SHORT"
                return row['Signal']
                
            df_ss['Action Status'] = df_ss.apply(get_action_status, axis=1)
            
            # --- COLOR CODING ---
            def highlight_rows(row):
                status = str(row['Action Status'])
                if 'LONG' in status:
                    return ['color: #00FF00; font-weight: bold'] * len(row)
                elif 'SHORT' in status:
                    return ['color: #FF4B4B; font-weight: bold'] * len(row)
                elif 'NO TRADE' in status:
                    return ['color: #808080'] * len(row)
                elif 'ERROR' in status:
                    return ['color: #FFA500'] * len(row)
                return [''] * len(row)

            display_cols = ['Symbol', 'Action Status', 'LTP', 'StopLoss', 'Risk %', 'Total Risk (₹)', 'Qty', 'ADX']
            styled_df = df_ss[display_cols].style.apply(highlight_rows, axis=1)
            st.dataframe(styled_df, hide_index=True, use_container_width=True)
            
            # --- EXECUTION TERMINAL ---
            st.markdown("#### 📝 Execution Terminal")
            with st.form("ss_commit"):
                confirmed_ss = []
                for _, r in df_ss.iterrows():
                    if "LONG" in r['Action Status'] or "SHORT" in r['Action Status']:
                        direction = "LONG" if "LONG" in r['Action Status'] else "SHORT"
                        qty_to_log = r['Qty'] if direction == "LONG" else -r['Qty']
                        
                        if st.checkbox(f"Execute {direction} on {r['Symbol']} (Qty: {abs(qty_to_log)} | Risk: ₹{r['Total Risk (₹)']})", key=f"ss_{r['Symbol']}"):
                            confirmed_ss.append({
                                'Symbol': r['Symbol'], 'Timeframe': tf, 'Entry_Price': r['LTP'], 'Qty': qty_to_log, 
                                'Date': get_now_ist().strftime('%Y-%m-%d %H:%M'), 'Status': 'OPEN'
                            })
                
                if st.form_submit_button("💾 LOG TO REGIME PORTFOLIO"):
                    if confirmed_ss:
                        try:
                            df_cur = conn.read(worksheet="REGIME_PORTFOLIO", ttl=0).dropna(how='all')
                            conn.update(worksheet="REGIME_PORTFOLIO", data=pd.concat([df_cur, pd.DataFrame(confirmed_ss)], ignore_index=True))
                            st.success("Committed to Regime Portfolio!"); time.sleep(1); st.rerun()
                        except Exception as e:
                            st.error("Error: Make sure you created a worksheet named exactly 'REGIME_PORTFOLIO' in your Google Sheet.")
                    else:
                        st.info("No trades selected to log.")

    st.write("---")
    st.subheader("🛰️ Active Trend War Room (Regime Portfolio)")
    
    @st.fragment(run_every="120s")
    def live_regime_war_room():
        if not is_market_open(): st.info("😴 Market Closed. Feeds paused.")
        try:
            df_reg = conn.read(worksheet="REGIME_PORTFOLIO", ttl=0).dropna(how='all')
            active_reg = df_reg[df_reg['Status'].astype(str).str.upper() == 'OPEN'].copy()
            
            if active_reg.empty: return st.write("No active regime trades.")

            with st.expander("📝 Manage Regime Trades & Exits"):
                with st.form("edit_regime_positions"):
                    upd_reg = []
                    c1, c2, c3, c4, c5 = st.columns([1.5, 1, 1.2, 1.2, 1.5])
                    c1.caption("Symbol"); c2.caption("Qty"); c3.caption("Entry"); c4.caption("Exit Price"); c5.caption("Action")
                    
                    for idx, r in active_reg.iterrows():
                        c1, c2, c3, c4, c5 = st.columns([1.5, 1, 1.2, 1.2, 1.5])
                        c1.markdown(f"**{r['Symbol']}**")
                        curr_qty = int(float(r['Qty'])) if 'Qty' in r and pd.notna(r['Qty']) else 0
                        new_q = c2.number_input("Qty", value=curr_qty, step=1, key=f"rq_{idx}", label_visibility="collapsed")
                        new_p = c3.number_input("Entry", value=float(r['Entry_Price']), step=0.05, key=f"rp_{idx}", label_visibility="collapsed")
                        exit_p = c4.number_input("Exit", value=0.00, step=0.05, key=f"rep_{idx}", label_visibility="collapsed")
                        new_s = c5.selectbox("Action", ["HOLD", "CLOSE TRADE"], index=0, key=f"rst_{idx}", label_visibility="collapsed")
                        upd_reg.append({'idx': idx, 'q': new_q, 'p': new_p, 'ep': exit_p, 's': new_s})
                    
                    if st.form_submit_button("✅ Update / Close Trades"):
                        for u in upd_reg:
                            df_reg.at[u['idx'], 'Qty'] = u['q']; df_reg.at[u['idx'], 'Entry_Price'] = u['p']
                            if u['s'] == "CLOSE TRADE":
                                df_reg.at[u['idx'], 'Status'] = "EXIT"; df_reg.at[u['idx'], 'Exit_Price'] = u['ep']
                        conn.update(worksheet="REGIME_PORTFOLIO", data=df_reg); st.rerun()

            rows = []; total_regime_pnl = 0.0
            for _, r in active_reg.iterrows():
                sym = r['Symbol']; entry = float(r['Entry_Price']); qty = int(float(r['Qty']))
                tf = r.get('Timeframe', '1d')
                live_data = calc_silent_signal(sym, interval=tf, period=period_map.get(tf, "1y"))
                
                if live_data:
                    ltp = live_data['LTP']; live_sl = live_data['StopLoss']
                else:
                    try:
                        ltp = yf.Ticker(f"{sym}.NS").history(period="1d")['Close'].iloc[-1]
                    except:
                        ltp = 0.0
                    live_sl = 0.0

                rupee_pnl = round((ltp - entry) * qty, 2)
                total_regime_pnl += rupee_pnl
                
                if qty > 0: action = "✅ HOLD" if ltp > live_sl else "🚨 EXIT (SL HIT)"
                else: action = "✅ HOLD" if ltp < live_sl else "🚨 EXIT (SL HIT)"
                
                pnl_display = f"🟢 ₹{rupee_pnl:,.2f}" if rupee_pnl >= 0 else f"🔴 -₹{abs(rupee_pnl):,.2f}"
                rows.append({"Symbol": sym, "Dir": "LONG" if qty > 0 else "SHORT", "Qty": abs(qty), "Entry": entry, "LTP": ltp, "Live SL": live_sl, "Live P&L": pnl_display, "Signal": action})
            
            st.metric("Total Regime P&L", f"₹{round(total_regime_pnl, 2):,}")
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
            for r in rows:
                if "EXIT" in r['Signal']: st.error(f"🚨 {r['Symbol']} has crossed its Stop Loss!")
                    
        except Exception as e:
            st.warning("No Regime Portfolio found. Commit a trade first to initialize.")

    live_regime_war_room()

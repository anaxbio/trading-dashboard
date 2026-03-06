import streamlit as st
import pandas as pd
import yfinance as yf
from streamlit_gsheets import GSheetsConnection
from datetime import datetime
import time
import pytz
from concurrent.futures import ThreadPoolExecutor
import requests
import io
import numpy as np # NEW: Required for ETF Volatility Math

# --- CONFIG & SETUP ---
st.set_page_config(page_title="EP Dual-Engine Cockpit", layout="wide")
st.title("🛡️ EP Strategy: Intraday 5X vs. Stage 2 Swing")

def get_now_ist():
    return datetime.now(pytz.timezone('Asia/Kolkata'))

def is_market_open():
    now = get_now_ist()
    if now.weekday() >= 5: return False
    mkt_start = now.replace(hour=9, minute=15, second=0, microsecond=0)
    mkt_end = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return mkt_start <= now <= mkt_end

# Connect to Google Sheets
conn = st.connection("gsheets", type=GSheetsConnection)

# --- DATA ENGINES (UNCHANGED) ---
def get_vwap_data(sym):
    try:
        t = yf.Ticker(f"{sym}.NS")
        df = t.history(period="1d", interval="1m")
        if df.empty or df['Volume'].sum() == 0: return 0.0, 0.0, 0.0
        df['TP'] = (df['High'] + df['Low'] + df['Close']) / 3
        vol_sum = df['Volume'].sum()
        vwap = (df['TP'] * df['Volume']).sum() / vol_sum
        ltp = df['Close'].iloc[-1]
        return round(ltp, 2), round(vwap, 2), 0.0
    except: return 0.0, 0.0, 0.0

def get_swing_stops(sym):
    try:
        t = yf.Ticker(f"{sym}.NS")
        hist = t.history(period="50d")
        if hist.empty: return 0.0, 0.0, 0.0
        hard_sl = hist['Low'].iloc[-1]
        trail_sl = hist['Close'].rolling(20).mean().iloc[-1]
        curr_ltp = hist['Close'].iloc[-1]
        return round(hard_sl, 2), round(trail_sl, 2), round(curr_ltp, 2)
    except: return 0.0, 0.0, 0.0

def process_ticker(sym, threshold, use_sma_wall):
    try:
        t = yf.Ticker(f"{sym}.NS")
        hist = t.history(period="1y")
        if len(hist) < 200: return None
        
        curr_p = hist['Close'].iloc[-1]
        sma200 = hist['Close'].rolling(200).mean().iloc[-1]
        
        if use_sma_wall and curr_p < (sma200 * 0.98): return None
        
        prev_c = hist['Close'].iloc[-2]
        day_h = hist['High'].iloc[-1]
        max_chg = ((day_h - prev_c) / prev_c) * 100
        avg_vol = hist['Volume'].tail(30).mean()
        rvol = hist['Volume'].iloc[-1] / (avg_vol if avg_vol > 0 else 1)
        
        if max_chg >= threshold and rvol > 1.2:
            _, vwap, _ = get_vwap_data(sym)
            if vwap == 0.0: vwap = curr_p 
            if curr_p < vwap: return None 
            
            sys_sl = round(vwap - 2.0, 2)
            dist_wall = round(((curr_p - sma200) / sma200) * 100, 2)
            
            return {
                'Symbol': sym, 'LTP': round(curr_p, 2), 
                'Max%': round(max_chg, 2), 'RVOL': round(rvol, 1), 
                'Dist_Wall%': dist_wall, 'Sys_SL': sys_sl
            }
    except: pass
    return None

def run_engine(threshold, use_sma_wall, universe="Nifty 500"):
    urls = [
        "https://archives.nseindia.com/content/indices/ind_nifty500list.csv",
        "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv"
    ] if universe == "Nifty 500" else ["https://archives.nseindia.com/content/indices/ind_niftymicrocap250list.csv"]
    
    tickers = []
    headers = {'User-Agent': 'Mozilla/5.0'}
    for url in urls:
        try:
            r = requests.get(url, headers=headers, timeout=5)
            if r.status_code == 200:
                tickers = pd.read_csv(io.StringIO(r.text))['Symbol'].tolist()
                break
        except: continue
    
    if not tickers: 
        st.error(f"Failed to fetch {universe}. NSE might be blocking the request.")
        return pd.DataFrame()

    results = []
    prog = st.progress(0, text=f"Scanning {universe}...")
    with ThreadPoolExecutor(max_workers=25) as executor:
        futures = [executor.submit(process_ticker, s, threshold, use_sma_wall) for s in tickers]
        for i, f in enumerate(futures):
            prog.progress((i+1)/len(tickers), text=f"Scanning {universe}... {i+1}/{len(tickers)}")
            res = f.result()
            if res: results.append(res)
    prog.empty()
    
    df = pd.DataFrame(results)
    if not df.empty:
        sort_col = 'Dist_Wall%' if use_sma_wall else 'Max%'
        df = df.sort_values(by=sort_col, ascending=False).reset_index(drop=True)
        df['Rank'] = "Laggard"
        df.loc[0:4, 'Rank'] = "🔥 LEADER"
        df = df.head(8)
    return df

# --- NEW: ETF MOMENTUM ENGINE ---
def calculate_etf_momentum(sym):
    try:
        t = yf.Ticker(f"{sym}.NS")
        hist = t.history(period="1y")
        if len(hist) < 250: return None
        
        p_curr = hist['Close'].iloc[-1]
        
        # Returns
        r_3m = (p_curr - hist['Close'].iloc[-63]) / hist['Close'].iloc[-63]
        r_6m = (p_curr - hist['Close'].iloc[-126]) / hist['Close'].iloc[-126]
        r_9m = (p_curr - hist['Close'].iloc[-189]) / hist['Close'].iloc[-189]
        r_12m = (p_curr - hist['Close'].iloc[-252]) / hist['Close'].iloc[-252]
        
        # Strategy Score
        score = (r_3m * 0.25) + (r_6m * 0.25) + (r_9m * 0.25) + (r_12m * 0.25)
        
        # Volatility (90-day annualized std dev)
        daily_rets = hist['Close'].pct_change().dropna()
        vol_90d = daily_rets.tail(90).std() * np.sqrt(252)
        
        if vol_90d == 0: return None
        
        return {
            'Symbol': sym, 'LTP': round(p_curr, 2), 'Momentum_Score': score,
            'Vol_90D': vol_90d, 'Inv_Vol': 1 / vol_90d
        }
    except: return None

# --- UI TABS (NOW 3 TABS) ---
tab1, tab2, tab3 = st.tabs(["🚀 INTRADAY 5X COCKPIT", "📈 STAGE 2 SWING", "🛡️ TACTICAL ETF ALIGNER"])

# ==========================================
# TAB 1: INTRADAY 5X (Velocity) -> UNCHANGED
# ==========================================
with tab1:
    st.subheader("Step 1: Intraday Hunter (SL = VWAP - ₹2.00)")
    
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
            
            st.dataframe(df_i[['Rank', 'Symbol', 'LTP', 'Max%', 'Sys_SL', 'Qty']], hide_index=True)
            
            with st.form("intra_commit"):
                confirmed = []
                for _, r in df_i.iterrows():
                    if r['Rank'] == "🔥 LEADER":
                        if st.checkbox(f"Trade {r['Symbol']} (Qty: {r['Qty']})", key=f"intra_{r['Symbol']}"):
                            confirmed.append({
                                'Symbol': r['Symbol'], 'Entry_Price': r['LTP'], 'Qty': r['Qty'], 
                                'Date': get_now_ist().strftime('%Y-%m-%d %H:%M'), 'Status': 'OPEN'
                            })
                if st.form_submit_button("💾 COMMIT TO WAR ROOM"):
                    df_cur = conn.read(worksheet="INTRADAY_PORTFOLIO", ttl=0).dropna(how='all')
                    conn.update(worksheet="INTRADAY_PORTFOLIO", data=pd.concat([df_cur, pd.DataFrame(confirmed)], ignore_index=True))
                    st.success("Committed!"); time.sleep(1); st.rerun()
        else:
            st.warning("🚨 0 stocks passed the VWAP Risk Filter. The market is chopping morning breakouts.")

    st.write("---")
    st.subheader("🛰️ Active War Room (Live P&L)")
    
    @st.fragment(run_every="120s")
    def live_intra():
        if not is_market_open(): st.info("😴 Market Closed. Live feeds paused.")
        try:
            df = conn.read(worksheet="INTRADAY_PORTFOLIO", ttl=0).dropna(how='all')
            active = df[df['Status'].astype(str).str.upper() == 'OPEN'].copy()
            
            if active.empty: return st.write("No active trades.")

            with st.expander("📝 Edit Prices, Qty & Status"):
                with st.form("edit_intra_positions"):
                    updated_rows = []
                    c1, c2, c3, c4 = st.columns([1.5, 1, 1.5, 1])
                    c1.caption("Symbol"); c2.caption("Qty"); c3.caption("Buy Price"); c4.caption("Status")
                    
                    for idx, r in active.iterrows():
                        c1, c2, c3, c4 = st.columns([1.5, 1, 1.5, 1])
                        c1.markdown(f"**{r['Symbol']}**")
                        curr_qty = int(float(r['Qty'])) if 'Qty' in r and pd.notna(r['Qty']) else 0
                        
                        new_q = c2.number_input("Qty", value=curr_qty, step=1, key=f"iq_{idx}", label_visibility="collapsed")
                        new_p = c3.number_input("Price", value=float(r['Entry_Price']), step=0.05, key=f"ip_{idx}", label_visibility="collapsed")
                        new_s = c4.selectbox("Status", ["OPEN", "EXIT"], index=0, key=f"ist_{idx}", label_visibility="collapsed")
                        updated_rows.append({'idx': idx, 'q': new_q, 'p': new_p, 's': new_s})
                    
                    if st.form_submit_button("✅ Update Ledger"):
                        for u in updated_rows:
                            df.at[u['idx'], 'Qty'] = u['q']
                            df.at[u['idx'], 'Entry_Price'] = u['p']
                            df.at[u['idx'], 'Status'] = u['s']
                        conn.update(worksheet="INTRADAY_PORTFOLIO", data=df)
                        st.rerun()

            rows = []
            total_session_pnl = 0.0

            for _, r in active.iterrows():
                ltp, vwap, _ = get_vwap_data(r['Symbol'])
                sys_sl = round(vwap - 2.0, 2)
                
                entry = float(r['Entry_Price'])
                qty = int(float(r['Qty'])) if 'Qty' in r and pd.notna(r['Qty']) else 0
                rupee_pnl = round((ltp - entry) * qty, 2)
                total_session_pnl += rupee_pnl
                
                pnl_display = f"🟢 ₹{rupee_pnl:,.2f}" if rupee_pnl >= 0 else f"🔴 -₹{abs(rupee_pnl):,.2f}"
                
                rows.append({
                    "Symbol": r['Symbol'], "Qty": qty, "Entry": entry, 
                    "LTP": ltp, "SL (V-2)": sys_sl, 
                    "Live P&L": pnl_display, 
                    "Signal": "✅ HOLD" if ltp > sys_sl else "🚨 EXIT NOW"
                })
            
            st.metric("Total Session P&L", f"₹{round(total_session_pnl, 2):,}", delta=f"{round(total_session_pnl, 2)}")
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
            
            for r in rows:
                if "EXIT NOW" in r['Signal']: st.error(f"🚨 {r['Symbol']} has broken the VWAP-2 SL!")
                    
        except Exception as e:
            st.error(f"War Room Sync Error: {e}")

    live_intra()

# ==========================================
# TAB 2: STAGE 2 SWING (Continuity) -> UNCHANGED
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
# TAB 3: NEW TACTICAL ETF ALIGNER
# ==========================================
with tab3:
    st.subheader("🛡️ Tactical ETF Momentum & Inverse Volatility Aligner")
    st.markdown("Automatically calculates momentum scores and allocates capital inversely to volatility, providing exact Buy/Sell execution targets.")
    
    # Define a robust NSE ETF Universe
    etf_universe = [
        'NIFTYBEES', 'BANKBEES', 'PSUBNKBEES', 'CPSEETF', 'GOLDBEES', 
        'SILVERBEES', 'ITBEES', 'PHARMABEES', 'MON100', 'MID150BEES', 
        'SMALLCAP', 'AUTOBEES', 'FMCGIETF', 'METALIETF'
    ]
    
    col_cash, col_scan = st.columns([1, 1])
    with col_cash:
        fresh_cash = st.number_input("Fresh Cash to Deploy (₹)", value=10000, step=1000)
    with col_scan:
        st.write("") # Spacing
        run_etf = st.button("🔄 Run Momentum & Volatility Scan")
        
    st.write("---")
    
    # 1. User Holdings Data Editor
    st.markdown("#### 1. Current Holdings")
    st.caption("Edit your actual locked units and current prices here. The app will calculate your live portfolio weights.")
    
    # Pre-populate with your examples, but make it fully editable
    default_holdings = pd.DataFrame([
        {"Symbol": "GOLDBEES", "Locked_Units": 3560, "Avg_Price": 26.42},
        {"Symbol": "PSUBNKBEES", "Locked_Units": 653, "Avg_Price": 104.14},
        {"Symbol": "METALIETF", "Locked_Units": 3585, "Avg_Price": 11.95},
        {"Symbol": "SILVERBEES", "Locked_Units": 126, "Avg_Price": 287.10}
    ])
    
    edited_holdings = st.data_editor(default_holdings, num_rows="dynamic", use_container_width=True)
    
    # Calculate Live Values
    edited_holdings['Live_Value'] = edited_holdings['Locked_Units'] * edited_holdings['Avg_Price']
    total_holdings_val = edited_holdings['Live_Value'].sum()
    total_portfolio_val = total_holdings_val + fresh_cash
    
    st.metric("Total Buying Power (Holdings + Cash)", f"₹{total_portfolio_val:,.2f}")
    
    # 2. Logic Engine & Results
    if run_etf:
        prog_etf = st.progress(0, text="Calculating ETF Momentum & Volatility...")
        etf_results = []
        
        for i, sym in enumerate(etf_universe):
            prog_etf.progress((i+1)/len(etf_universe), text=f"Analyzing {sym}...")
            res = calculate_etf_momentum(sym)
            if res: etf_results.append(res)
            
        prog_etf.empty()
        
        if etf_results:
            df_etf = pd.DataFrame(etf_results)
            df_etf = df_etf.sort_values(by='Momentum_Score', ascending=False).reset_index(drop=True)
            
            # Take Top 4 for Tactical Allocation
            top_4 = df_etf.head(4).copy()
            
            # Calculate Target Weights using Inverse Volatility
            sum_inv_vol = top_4['Inv_Vol'].sum()
            top_4['Target_Weight_%'] = (top_4['Inv_Vol'] / sum_inv_vol) * 100
            
            st.markdown("#### 2. Momentum Leaderboard (Top 4 Picks)")
            st.dataframe(
                top_4[['Symbol', 'LTP', 'Momentum_Score', 'Vol_90D', 'Target_Weight_%']], 
                column_config={
                    "Momentum_Score": st.column_config.NumberColumn(format="%.3f"),
                    "Vol_90D": st.column_config.NumberColumn("90-Day Vol", format="%.3f"),
                    "Target_Weight_%": st.column_config.ProgressColumn("Ideal Allocation", format="%.1f%%", min_value=0, max_value=100)
                },
                hide_index=True, use_container_width=True
            )
            
            st.markdown("#### 3. Execution Terminal")
            st.caption("Calculates exactly how many units to Buy/Sell to match the Target Weights.")
            
            exec_rows = []
            for _, r in top_4.iterrows():
                sym = r['Symbol']
                target_weight = r['Target_Weight_%'] / 100
                ideal_capital = total_portfolio_val * target_weight
                
                # Find current value if it exists in holdings
                current_val = 0
                if sym in edited_holdings['Symbol'].values:
                    current_val = edited_holdings.loc[edited_holdings['Symbol'] == sym, 'Live_Value'].values[0]
                
                # Calculate Gap and Units
                capital_gap = ideal_capital - current_val
                units_to_transact = int(capital_gap / r['LTP'])
                
                action = "BUY" if units_to_transact > 0 else "SELL"
                if units_to_transact == 0: action = "HOLD"
                
                exec_rows.append({
                    "Symbol": sym,
                    "Target Allocation": f"₹{ideal_capital:,.2f}",
                    "Current Value": f"₹{current_val:,.2f}",
                    "Action": action,
                    "Units": abs(units_to_transact),
                    "Capital Required": f"₹{capital_gap:,.2f}"
                })
            
            st.dataframe(pd.DataFrame(exec_rows), hide_index=True, use_container_width=True)

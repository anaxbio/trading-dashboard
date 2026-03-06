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

# --- DATA ENGINES ---
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
        
        # Stage 2 Check (Only for Swing)
        if use_sma_wall and curr_p < (sma200 * 0.98): return None
        
        prev_c = hist['Close'].iloc[-2]
        day_h = hist['High'].iloc[-1]
        max_chg = ((day_h - prev_c) / prev_c) * 100
        avg_vol = hist['Volume'].tail(30).mean()
        rvol = hist['Volume'].iloc[-1] / (avg_vol if avg_vol > 0 else 1)
        
        # EP Breakout Check
        if max_chg >= threshold and rvol > 1.2:
            _, vwap, _ = get_vwap_data(sym)
            if vwap == 0.0: vwap = curr_p 
            
            # 🚨 STRICT FILTER: Ignore if broken below VWAP
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
        st.error(f"Failed to fetch {universe} list. NSE might be blocking the request.")
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

# --- UI TABS ---
tab1, tab2 = st.tabs(["🚀 INTRADAY 5X COCKPIT", "📈 STAGE 2 SWING"])

# ==========================================
# TAB 1: INTRADAY 5X (Velocity)
# ==========================================
with tab1:
    st.subheader("Step 1: Intraday Hunter (SL = VWAP - ₹2.00)")
    
    col_cap, col_info = st.columns([2, 1])
    with col_cap:
        intra_capital = st.slider("Total Buying Power (₹) [Incl. 5X Leverage]", 10000, 1000000, 100000, 10000)
    with col_info:
        cash_needed = int(intra_capital / 5)
        st.metric("Required Cash Margin", f"₹{cash_needed:,}")

    if st.button("🔥 Scan Intraday Movers"):
        st.session_state.intra_results = run_engine(4.0, use_sma_wall=False)
    
    if 'intra_results' in st.session_state and not st.session_state.intra_results.empty:
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

    st.write("---")
    st.subheader("🛰️ Active War Room (Live P&L)")
    
    @st.fragment(run_every="120s")
    def live_intra():
        if not is_market_open():
            st.warning("😴 Market Closed. Live feeds paused.")
        try:
            df = conn.read(worksheet="INTRADAY_PORTFOLIO", ttl=0).dropna(how='all')
            active = df[df['Status'].astype(str).str.upper() == 'OPEN'].copy()
            
            if active.empty:
                return st.info("No active trades.")

            # --- Live Position Editor ---
            with st.expander("📝 Edit Prices / Close Trades"):
                with st.form("edit_positions"):
                    updated_rows = []
                    for idx, r in active.iterrows():
                        c1, c2, c3 = st.columns([2, 2, 2])
                        c1.markdown(f"**{r['Symbol']}**")
                        new_p = c2.number_input("Entry Price", value=float(r['Entry_Price']), step=0.05, key=f"p_{idx}", label_visibility="collapsed")
                        new_s = c3.selectbox("Status", ["OPEN", "EXIT"], index=0, key=f"st_{idx}", label_visibility="collapsed")
                        updated_rows.append({'idx': idx, 'p': new_p, 's': new_s})
                    
                    if st.form_submit_button("✅ Update Ledger"):
                        for u in updated_rows:
                            df.at[u['idx'], 'Entry_Price'] = u['p']
                            df.at[u['idx'], 'Status'] = u['s']
                        conn.update(worksheet="INTRADAY_PORTFOLIO", data=df)
                        st.rerun()

            # --- Live P&L Calculation Engine ---
            rows = []
            total_session_pnl = 0.0

            for _, r in active.iterrows():
                ltp, vwap, _ = get_vwap_data(r['Symbol'])
                sys_sl = round(vwap - 2.0, 2)
                
                entry = float(r['Entry_Price'])
                qty = int(r.get('Qty', 0))
                rupee_pnl = round((ltp - entry) * qty, 2)
                total_session_pnl += rupee_pnl
                
                rows.append({
                    "Symbol": r['Symbol'], "Qty": qty, "Entry": entry, 
                    "LTP": ltp, "VWAP": vwap, "SL (V-2)": sys_sl, 
                    "Live P&L (₹)": rupee_pnl, 
                    "Signal": "✅ HOLD" if ltp > sys_sl else "🚨 EXIT NOW"
                })
            
            # --- Display Metrics & Table ---
            st.metric("Total Session P&L (₹)", f"₹{round(total_session_pnl, 2):,}", delta=f"{round(total_session_pnl, 2)}")
            
            st.dataframe(
                pd.DataFrame(rows),
                column_config={
                    "Live P&L (₹)": st.column_config.NumberColumn("Live P&L (₹)", format="₹ %.2f")
                },
                hide_index=True,
                use_container_width=True
            )
            
            # Auto-alert if something breaks SL
            for r in rows:
                if "EXIT NOW" in r['Signal']:
                    st.error(f"🚨 ALERT: {r['Symbol']} has broken the VWAP-2 SL!")
                    
        except Exception as e:
            st.error(f"War Room Sync Error: {e}")

    live_intra()


# ==========================================
# TAB 2: STAGE 2 SWING (Continuity)
# ==========================================
with tab2:
    st.subheader("Step 1: Swing Engine")
    
    col_u, col_b = st.columns([2, 1])
    with col_u:
        choice = st.radio("Target Universe:", ["Nifty 500", "Microcap 250"], horizontal=True)
    with col_b:
        swing_alloc = st.number_input("Budget Per Stock (₹)", 5000, 500000, 20000, 5000)
    
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
            sw_rows = []
            for idx, r in active_sw.iterrows():
                hard, trail, ltp = get_swing_stops(r['Symbol'])
                entry = float(r['Entry_Price'])
                qty = int(r.get('Qty', 0))
                pnl = round((ltp - entry) * qty, 2)
                sw_rows.append({
                    "Symbol": r['Symbol'], "Entry": entry, "Qty": qty, "LTP": ltp, 
                    "P&L (₹)": pnl, "HARD SL": hard, "TRAIL SL": trail
                })
            
            # Position Editor for Swing trades
            with st.expander("📝 Close Swing Trades"):
                with st.form("edit_swing"):
                    sw_upd = []
                    for idx, r in active_sw.iterrows():
                        c1, c2 = st.columns([3, 1])
                        c1.write(f"{r['Symbol']} - Bought at ₹{r['Entry_Price']}")
                        s = c2.selectbox("Status", ["OPEN", "EXIT"], index=0, key=f"sws_{idx}", label_visibility="collapsed")
                        sw_upd.append({'idx': idx, 's': s})
                    if st.form_submit_button("Update Status"):
                        for u in sw_upd:
                            df_sw.at[u['idx'], 'Status'] = u['s']
                        conn.update(worksheet="SWING_PORTFOLIO", data=df_sw)
                        st.rerun()

            st.dataframe(
                pd.DataFrame(sw_rows),
                column_config={"P&L (₹)": st.column_config.NumberColumn("P&L (₹)", format="₹ %.2f")},
                hide_index=True, use_container_width=True
            )
        else:
            st.info("Swing portfolio empty.")
    except Exception as e:
        st.error(f"Sync Error: {e}")

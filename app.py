import streamlit as st
import pandas as pd
import yfinance as yf
from streamlit_gsheets import GSheetsConnection
from datetime import datetime
import time
import pytz
from concurrent.futures import ThreadPoolExecutor

# --- CONFIG ---
st.set_page_config(page_title="EP Dual-Engine Cockpit", layout="wide")
st.title("🛡️ EP Strategy: Intraday 5X vs. Stage 2 Swing")

def get_now_ist():
    return datetime.now(pytz.timezone('Asia/Kolkata'))

conn = st.connection("gsheets", type=GSheetsConnection)

# --- DATA ENGINES ---
def get_vwap_data(sym):
    try:
        t = yf.Ticker(f"{sym}.NS")
        df = t.history(period="1d", interval="1m")
        if df.empty or df['Volume'].sum() == 0: return 0.0, 0.0
        df['TP'] = (df['High'] + df['Low'] + df['Close']) / 3
        vwap = (df['TP'] * df['Volume']).sum() / df['Volume'].sum()
        return round(df['Close'].iloc[-1], 2), round(vwap, 2)
    except: return 0.0, 0.0

def process_ticker(sym, threshold, use_sma_wall):
    try:
        t = yf.Ticker(f"{sym}.NS")
        hist = t.history(period="1y")
        if len(hist) < 200: return None
        
        curr_p = hist['Close'].iloc[-1]
        sma200 = hist['Close'].rolling(200).mean().iloc[-1]
        dist_from_wall = ((curr_p - sma200) / sma200) * 100
        
        if use_sma_wall and curr_p < sma200: return None
        
        prev_c = hist['Close'].iloc[-2]
        day_h = hist['High'].iloc[-1]
        max_chg = ((day_h - prev_c) / prev_c) * 100
        
        avg_vol = hist['Volume'].tail(30).mean()
        rvol = hist['Volume'].iloc[-1] / avg_vol
        
        if max_chg >= threshold and rvol > 1.5:
            return {
                'Symbol': sym, 
                'LTP': round(curr_p, 2), 
                'Max%': round(max_chg, 2), 
                'RVOL': round(rvol, 1),
                'Dist_Wall%': round(dist_from_wall, 2)
            }
    except: pass
    return None

def run_engine(threshold, use_sma_wall):
    url = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
    try:
        tickers = pd.read_csv(url)['Symbol'].tolist()
    except: return pd.DataFrame()
    
    results = []
    prog = st.progress(0)
    with ThreadPoolExecutor(max_workers=25) as executor:
        futures = [executor.submit(process_ticker, s, threshold, use_sma_wall) for s in tickers]
        for i, f in enumerate(futures):
            prog.progress((i+1)/len(tickers))
            res = f.result()
            if res: results.append(res)
    prog.empty()
    
    df = pd.DataFrame(results)
    if not df.empty and use_sma_wall:
        df = df.sort_values(by='Dist_Wall%', ascending=False).reset_index(drop=True)
        df['Rank'] = "Laggard"
        df.loc[0:4, 'Rank'] = "🔥 LEADER"
        df.loc[5:7, 'Rank'] = "⚠️ LAGGARD"
        df = df.head(8)
    return df

# --- UI TABS ---
tab1, tab2 = st.tabs(["🚀 INTRADAY 5X COCKPIT", "📈 STAGE 2 SWING"])

# --- TAB 1: INTRADAY 5X ---
with tab1:
    st.subheader("Step 1: Intraday Hunter (No Wall)")
    if st.button("🔥 Scan Intraday Movers (>4%)"):
        st.session_state.intra_results = run_engine(4.0, use_sma_wall=False)
    
    if 'intra_results' in st.session_state and not st.session_state.intra_results.empty:
        st.dataframe(st.session_state.intra_results)
        with st.form("intra_commit"):
            confirmed = []
            for i, row in st.session_state.intra_results.iterrows():
                if st.checkbox(f"Trade {row['Symbol']} (RVOL: {row['RVOL']})", key=f"intra_{row['Symbol']}"):
                    confirmed.append({
                        'Symbol': row['Symbol'], 'Entry_Price': row['LTP'], 
                        'Date': get_now_ist().strftime('%Y-%m-%d %H:%M'), 'Status': 'OPEN'
                    })
            if st.form_submit_button("💾 COMMIT TO 5X PORTFOLIO"):
                df_i = conn.read(worksheet="INTRADAY_PORTFOLIO", ttl=0).dropna(how='all')
                conn.update(worksheet="INTRADAY_PORTFOLIO", data=pd.concat([df_i, pd.DataFrame(confirmed)], ignore_index=True))
                st.success("Loaded to 5X Cockpit!")
                time.sleep(1); st.rerun()

    st.divider()
    st.subheader("Step 2: 5X Leverage Monitor (Auto-updates 2m)")
    @st.fragment(run_every="120s")
    def live_intra():
        try:
            df = conn.read(worksheet="INTRADAY_PORTFOLIO", ttl=0).dropna(how='all')
            active = df[df['Status'].astype(str).str.upper() == 'OPEN'].copy()
            if not active.empty:
                rows = []
                for _, r in active.iterrows():
                    ltp, vwap = get_vwap_data(r['Symbol'])
                    sys_sl = round(vwap * 0.999, 2)
                    cap_pnl = ((ltp - float(r['Entry_Price'])) / float(r['Entry_Price'])) * 500
                    rows.append({
                        "Symbol": r['Symbol'], "LTP": ltp, "Entry": r['Entry_Price'], 
                        "SYSTEM SL": sys_sl, "5X P&L%": f"{round(cap_pnl, 2)}%", 
                        "Signal": "✅" if ltp > sys_sl else "🚨 EXIT"
                    })
                st.table(pd.DataFrame(rows))
        except: pass
    live_intra()

# --- TAB 2: STAGE 2 SWING ---
with tab2:
    st.subheader("Step 1: Swing Engine (₹1 Lakh Portfolio Model)")
    if st.button("🚀 Scan for Swing Leaders (>5%)"):
        st.session_state.swing_results = run_engine(5.0, use_sma_wall=True)
    
    if 'swing_results' in st.session_state and not st.session_state.swing_results.empty:
        df_res = st.session_state.swing_results
        df_res['Qty_for_20k'] = (20000 / df_res['LTP']).astype(int)
        
        st.write("### Top Picks (Sorted by Strength)")
        cols_to_show = [c for c in ['Rank', 'Symbol', 'LTP', 'Dist_Wall%', 'Qty_for_20k'] if c in df_res.columns]
        st.table(df_res[cols_to_show])
        
        with st.form("swing_commit"):
            confirmed_s = []
            for i, row in df_res.iterrows():
                if 'Rank' in row and row['Rank'] == "🔥 LEADER":
                    qty = row.get('Qty_for_20k', 0)
                    if st.checkbox(f"Allocate ₹20k to {row['Symbol']} ({qty} shares)", key=f"sw_{row['Symbol']}"):
                        confirmed_s.append({
                            'Symbol': row['Symbol'], 'Entry_Price': row['LTP'], 
                            'Date': get_now_ist().strftime('%Y-%m-%d'), 'Status': 'OPEN'
                        })
            
            if st.form_submit_button("💾 COMMIT LEADERS TO SHEETS"):
                if confirmed_s:
                    df_s = conn.read(worksheet="SWING_PORTFOLIO", ttl=0).dropna(how='all')
                    conn.update(worksheet="SWING_PORTFOLIO", data=pd.concat([df_s, pd.DataFrame(confirmed_s)], ignore_index=True))
                    st.success("Swing Leaders committed!")
                    time.sleep(1); st.rerun()
                else:
                    st.warning("Please select a Leader to proceed.")

    st.divider()
    st.subheader("Step 2: Swing Portfolio")
    try:
        df_sw = conn.read(worksheet="SWING_PORTFOLIO", ttl=0).dropna(how='all')
        active_sw = df_sw[df_sw['Status'].astype(str).str.upper() == 'OPEN']
        if not active_sw.empty: 
            st.table(active_sw[['Symbol', 'Entry_Price', 'Date']])
    except: st.info("Swing portfolio empty.")

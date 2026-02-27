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

def is_market_open():
    now = get_now_ist()
    if now.weekday() >= 5: return False
    mkt_start = now.replace(hour=9, minute=15, second=0, microsecond=0)
    mkt_end = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return mkt_start <= now <= mkt_end

conn = st.connection("gsheets", type=GSheetsConnection)

# --- DATA ENGINES ---
def get_vwap_data(sym):
    try:
        t = yf.Ticker(f"{sym}.NS")
        df = t.history(period="1d", interval="1m")
        if df.empty or df['Volume'].sum() == 0: return 0.0, 0.0, 0.0
        df['TP'] = (df['High'] + df['Low'] + df['Close']) / 3
        vwap = (df['TP'] * df['Volume']).sum() / df['Volume'].sum()
        ltp = df['Close'].iloc[-1]
        dist_vwap = ((ltp - vwap) / vwap) * 100
        return round(ltp, 2), round(vwap, 2), round(dist_vwap, 2)
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
            dist_wall = ((curr_p - sma200) / sma200) * 100
            return {'Symbol': sym, 'LTP': round(curr_p, 2), 'Max%': round(max_chg, 2), 'RVOL': round(rvol, 1), 'Dist_Wall%': round(dist_wall, 2)}
    except: pass
    return None

def run_engine(threshold, use_sma_wall, universe="Nifty 500"):
    urls = {
        "Nifty 500": "https://archives.nseindia.com/content/indices/ind_nifty500list.csv",
        "Microcap 250": "https://archives.nseindia.com/content/indices/ind_niftymicrocap250list.csv"
    }
    try:
        tickers = pd.read_csv(urls.get(universe, urls["Nifty 500"]))['Symbol'].tolist()
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
    if not df.empty:
        sort_col = 'Dist_Wall%' if use_sma_wall else 'Max%'
        df = df.sort_values(by=sort_col, ascending=False).reset_index(drop=True)
        df['Rank'] = "Laggard"
        df.loc[0:4, 'Rank'] = "🔥 LEADER"
        df.loc[5:7, 'Rank'] = "⚠️ LAGGARD"
        df = df.head(8)
    return df

# --- UI TABS ---
tab1, tab2 = st.tabs(["🚀 INTRADAY 5X COCKPIT", "📈 STAGE 2 SWING"])

# --- TAB 1: INTRADAY 5X ---
with tab1:
    st.subheader("Step 1: Intraday Hunter (5X Leverage Model)")
    if st.button("🔥 Scan Intraday Movers"):
        st.session_state.intra_results = run_engine(4.0, use_sma_wall=False)
    
    if 'intra_results' in st.session_state and not st.session_state.intra_results.empty:
        df_i = st.session_state.intra_results
        df_i['Max_Qty_5X'] = (100000 / df_i['LTP']).astype(int)
        cols = [c for c in ['Rank', 'Symbol', 'LTP', 'Max%', 'Max_Qty_5X'] if c in df_i.columns]
        st.table(df_i[cols])
        
        with st.form("intra_commit"):
            confirmed = []
            for i, row in df_i.iterrows():
                if row.get('Rank') == "🔥 LEADER":
                    if st.checkbox(f"Trade {row['Symbol']} (Qty: {row.get('Max_Qty_5X', 0)})", key=f"intra_{row['Symbol']}"):
                        confirmed.append({'Symbol': row['Symbol'], 'Entry_Price': row['LTP'], 'Date': get_now_ist().strftime('%Y-%m-%d %H:%M'), 'Status': 'OPEN'})
            if st.form_submit_button("💾 COMMIT TO 5X PORTFOLIO"):
                df_cur = conn.read(worksheet="INTRADAY_PORTFOLIO", ttl=0).dropna(how='all')
                conn.update(worksheet="INTRADAY_PORTFOLIO", data=pd.concat([df_cur, pd.DataFrame(confirmed)], ignore_index=True))
                st.success("Committed!"); time.sleep(1); st.rerun()

    st.write("---")
    @st.fragment(run_every="120s")
    def live_intra():
        if not is_market_open():
            st.info("😴 Market Closed. Refresh Paused.")
            return
        try:
            df = conn.read(worksheet="INTRADAY_PORTFOLIO", ttl=0).dropna(how='all')
            active = df[df['Status'].

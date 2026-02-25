import streamlit as st
import pandas as pd
import yfinance as yf
from streamlit_gsheets import GSheetsConnection
from datetime import datetime
import time

# --- CONFIG ---
st.set_page_config(page_title="EP Monitor", layout="wide")
st.title("🚀 EP Stage 2 Tracker")

# Persist scanner results across refreshes
if 'scan_results' not in st.session_state:
    st.session_state.scan_results = pd.DataFrame()

conn = st.connection("gsheets", type=GSheetsConnection)

def get_2min_strategy_data(symbol):
    ticker_sym = str(symbol).strip().upper()
    if not ticker_sym.endswith(".NS"): ticker_sym += ".NS"
    try:
        # Fetching fresh data to ensure accurate 2m close
        df = yf.download(ticker_sym, period="1d", interval="2m", progress=False)
        if not df.empty:
            tp = (df['High'] + df['Low'] + df['Close']) / 3
            current_vwap = (tp * df['Volume']).cumsum() / df['Volume'].cumsum()
            return {'LTP': round(df['Close'].iloc[-1], 2), 'VWAP': round(current_vwap.iloc[-1], 2)}
    except: pass
    return {'LTP': 0, 'VWAP': 0}

def run_scan(threshold):
    url = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
    try:
        tickers = pd.read_csv(url)['Symbol'].tolist()
    except: return pd.DataFrame()
    results = []
    prog = st.progress(0)
    for i, sym in enumerate(tickers[:120]):
        prog.progress(i / 120)
        try:
            t = yf.Ticker(f"{sym}.NS")
            hist = t.history(period="1y")
            if len(hist) > 200:
                sma200 = hist['Close'].rolling(200).mean().iloc[-1]
                curr_p = hist['Close'].iloc[-1]
                prev_c = hist['Close'].iloc[-2]
                day_chg = ((curr_p - prev_c) / prev_c) * 100
                if curr_p > (sma200 * 0.98) and day_chg >= threshold:
                    results.append({'Symbol': sym, 'Entry_Price': round(curr_p, 2), 'Day %': round(day_chg, 2)})
        except: continue
    prog.empty()
    return pd.DataFrame(results)

# --- UI TABS ---
tab1, tab2, tab3 = st.tabs(["🚀 Scanner", "💰 Intraday", "📈 Swing"])

with tab1:
    st.header("Daily Scanner")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("🎯 Run 5% Scan"): st.session_state.scan_results = run_scan(5.0)
    with c2:
        if st.button("✅ Run 3.5% Scan"): st.session_state.scan_results = run_scan(3.5)

    if not st.session_state.scan_results.empty:
        confirmed = []
        for i, row in st.session_state.scan_results.iterrows():
            if st.checkbox(f"Add {row['Symbol']} (@ {row['Entry_Price']})", key=f"c_{row['Symbol']}"):
                # Adding Timestamp here
                now_ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                confirmed.append({'Symbol': row['Symbol'], 'Entry_Price': row['Entry_Price'], 'Date': now_ts, 'Status': 'OPEN'})
        
        if confirmed:
            mode = st.radio("Portfolio Type:", ["INTRADAY_PORTFOLIO", "SWING_PORTFOLIO"])
            if st.button("💾 Commit Trades"):
                try:
                    # Clear cache before reading to avoid 'Sheet not found' errors
                    st.cache_data.clear()
                    df = conn.read(worksheet=mode, ttl=0).dropna(how='all')
                    updated = pd.concat([df, pd.DataFrame(confirmed)], ignore_index=True)
                    conn.update(worksheet=mode, data=updated)
                    st.success(f"Saved to {mode} at {datetime.now().strftime('%H:%M:%S')}!")
                    st.session_state.scan_results = pd.DataFrame()
                    time.sleep(1) # Give GSheets a second to process
                    st.rerun()
                except Exception as e: st.error(f"Commit Failed: {e}")

with tab2:
    st.header("Intraday Monitor")
    if st.button("🔄 Force Refresh Data"): 
        st.cache_data.clear()
        st.rerun()
    try:
        df_i = conn.read(worksheet="INTRADAY_PORTFOLIO", ttl=0).dropna(how='all')
        if not df_i.empty:
            df_i['Status'] = df_i['Status'].astype(str).str.upper().str.strip()
            active_i = df_i[df_i['Status'] == 'OPEN'].copy()
            if not active_i.empty:
                l, v, s = [], [], []
                for sym in active_i['Symbol']:
                    res = get_2min_strategy_data(sym)
                    l.append(res['LTP']); v.append(res['VWAP'])
                    s.append("🚨 EXIT" if res['LTP'] < res['VWAP'] and res['LTP'] > 0 else "✅ OK")
                active_i['2m Close'], active_i['VWAP'], active_i['Signal'] = l, v, s
                st.table(active_i[['Symbol', 'Entry_Price', '2m Close', 'VWAP', 'Signal', 'Date']])
    except: st.warning("Connecting to 'INTRADAY_PORTFOLIO'...")

with tab3:
    st.header("Swing Monitor")
    if st.button("🔄 Force Refresh Swing"): 
        st.cache_data.clear()
        st.rerun()
    try:
        df_s = conn.read(worksheet="SWING_PORTFOLIO", ttl=0).dropna(how='all')
        if not df_s.empty:
            df_s['Status'] = df_s['Status'].astype(str).str.upper().str.strip()
            active_s = df_s[df_s['Status'] == 'OPEN'].copy()
            if not active_s.empty:
                p, sig = [], []
                for idx, row in active_s.iterrows():
                    curr = get_2min_strategy_data(row['Symbol'])['LTP']
                    sl = float(row['Entry_Price']) * 0.93
                    p.append(curr)
                    sig.append("🚨 SELL" if curr < sl and curr > 0 else "✅ OK")
                active_s['Price'], active_s['Signal'] = p, sig
                st.table(active_s[['Symbol', 'Entry_Price', 'Price', 'Signal', 'Date']])
    except: st.warning("Connecting to 'SWING_PORTFOLIO'...")

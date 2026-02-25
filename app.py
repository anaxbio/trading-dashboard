import streamlit as st
import pandas as pd
import requests
import yfinance as yf
from streamlit_gsheets import GSheetsConnection
from datetime import datetime

# --- CONFIG ---
st.set_page_config(page_title="EP Monitor", layout="wide")
st.title("🚀 EP Stage 2 Tracker")

# Session State Initialization
if 'scan_results' not in st.session_state:
    st.session_state.scan_results = pd.DataFrame()

# Connection (Uses Streamlit Secrets)
conn = st.connection("gsheets", type=GSheetsConnection)

def get_live_stats(symbol):
    """Self-healing price fetcher: Cleans symbols automatically."""
    clean_sym = str(symbol).split('.')[0].split('-')[0].strip().upper()
    try:
        url = f"https://priceapi.moneycontrol.com/pricefeed/nse/equityinst/{clean_sym}"
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, headers=headers, timeout=5).json()
        if res.get('msg') == 'success' and 'data' in res:
            return {'LTP': float(res['data']['lastPrice']), 'VWAP': float(res['data']['averagePrice'])}
    except:
        pass
    return {'LTP': 0, 'VWAP': 0}

def run_scan(threshold):
    """Scans Nifty 500 for breakouts."""
    url = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
    try:
        tickers = pd.read_csv(url)['Symbol'].tolist()
    except:
        return pd.DataFrame()
    results = []
    prog = st.progress(0)
    for i, sym in enumerate(tickers[:120]):
        prog.progress(i / 120)
        try:
            t = yf.Ticker(f"{sym}.NS")
            hist = t.history(period="1y")
            if len(hist) > 200:
                sma200 = hist['Close'].rolling(200).mean().iloc[-1]
                prev_c = hist['Close'].iloc[-2]
                curr_p = hist['Close'].iloc[-1]
                day_chg = ((curr_p - prev_c) / prev_c) * 100
                if curr_p > (sma200 * 0.98) and day_chg >= threshold:
                    results.append({'Symbol': sym, 'Entry': round(curr_p, 2), 'Day %': round(day_chg, 2)})
        except:
            continue
    prog.empty()
    return pd.DataFrame(results)

# --- TABS ---
tab1, tab2, tab3 = st.tabs(["🚀 Scanner", "💰 Intraday", "📈 Swing"])

with tab1:
    st.header("Step 1: Daily Scanner")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("🎯 Run 5% Primary Scan"):
            st.session_state.scan_results = run_scan(5.0)
    with c2:
        if st.button("✅ Run 3.5% Strength Scan"):
            st.session_state.scan_results = run_scan(3.5)

    if not st.session_state.scan_results.empty:
        confirmed = []
        for i, row in st.session_state.scan_results.iterrows():
            if st.checkbox(f"Add {row['Symbol']} (@ {row['Entry']})", key=f"check_{row['Symbol']}"):
                confirmed.append({'Symbol': row['Symbol'], 'Entry': row['Entry'], 'Date': datetime.now().strftime('%Y-%m-%d'), 'Status': 'OPEN'})
        if confirmed:
            mode = st.radio("Choose Portfolio:", ["INTRADAY_PORTFOLIO", "SWING_PORTFOLIO"])
            if st.button("💾 Commit to Google Sheet"):
                try:
                    try:
                        old_df = conn.read(worksheet=mode, ttl=0)
                    except:
                        old_df = pd.DataFrame(columns=['Symbol', 'Entry', 'Date', 'Status'])
                    updated = pd.concat([old_df, pd.DataFrame(confirmed)], ignore_index=True).drop_duplicates(subset=['Symbol', 'Date'])
                    conn.update(worksheet=mode, data=updated)
                    st.success("Saved!")
                    st.session_state.scan_results = pd.DataFrame()
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")

with tab2:
    st.header("Intraday Monitor")
    if st.button("🔄 Refresh", key="ri"): st.rerun()
    try:
        df_i = conn.read(worksheet="INTRADAY_PORTFOLIO", ttl=0)
        if not df_i.empty:
            active_i = df_i[df_i['Status'].astype(str).str.upper().str.strip() == 'OPEN'].copy()
            if not active_i.empty:
                ltps, vwaps, signals = [], [], []
                for s in active_i['Symbol']:
                    stats = get_live_stats(s)
                    ltps.append(stats['LTP'])
                    vwaps.append(stats['VWAP'])
                    signals.append("🚨 EXIT" if stats['LTP'] < stats['VWAP'] and stats['LTP'] > 0 else "✅ OK")
                active_i['LTP'], active_i['VWAP'], active_i['Signal'] = ltps, vwaps, signals
                st.table(active_i[['Symbol', 'Entry', 'LTP', 'VWAP', 'Signal']])
    except: st.info("Intraday Ledger empty.")

with tab3:
    st.header("Swing Monitor")
    if st.button("🔄 Refresh", key="rs"): st.rerun()
    try:
        df_s = conn.read(worksheet="SWING_PORTFOLIO", ttl=0)
        if not df_s.empty:
            active_s = df_s[df_s['Status'].astype(str).str.upper().str.strip() == 'OPEN'].copy()
            if not active_s.empty:
                ltp_s, signals_s = [], []
                for idx, row in active_s.iterrows():
                    curr_ltp = get_live_stats(row['Symbol'])['LTP']
                    sl_level = float(row['Entry']) * 0.93
                    ltp_s.append(curr_ltp)
                    signals_s.append("🚨 SELL" if curr_ltp < sl_level and curr_ltp > 0 else "✅ OK")
                active_s['LTP'], active_s['Signal'] = ltp_s, signals_s
                st.table(active_s[['Symbol', 'Entry', 'LTP', 'Signal']])
    except: st.info("Swing Ledger empty.")

import streamlit as st
import pandas as pd
import requests
import yfinance as yf
from streamlit_gsheets import GSheetsConnection
from datetime import datetime

# --- CONFIGURATION ---
st.set_page_config(page_title="EP Stage 2 Monitor", layout="wide")
st.title("🚀 EP Stage 2: Universal Scanner & Exit Monitor")

# Initialize session state for the scanner stages
if 'scan_stage' not in st.session_state:
    st.session_state.scan_stage = "idle" # Options: idle, first_failed, results_found

# Connect to Google Sheets
try:
    conn = st.connection("gsheets", type=GSheetsConnection)
except:
    st.error("Google Sheets connection not found. Check Secrets.")

# --- DATA FETCHING ENGINE ---
def get_live_stats(symbol):
    clean_sym = str(symbol).split('-')[0].strip()
    try:
        url = f"https://priceapi.moneycontrol.com/pricefeed/nse/equityinst/{clean_sym}"
        res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5).json()
        if res['msg'] == 'success':
            return {'LTP': float(res['data']['lastPrice']), 'VWAP': float(res['data']['averagePrice']), 'Src': 'MC'}
    except:
        pass
    
    try:
        t = yf.Ticker(f"{clean_sym}.NS")
        df = t.history(period="1d", interval="2m")
        if not df.empty:
            ltp = df['Close'].iloc[-1]
            vwap = (df['Close'] * df['Volume']).sum() / df['Volume'].sum()
            return {'LTP': round(ltp, 2), 'VWAP': round(vwap, 2), 'Src': 'YF'}
    except:
        return {'LTP': 0, 'VWAP': 0, 'Src': 'ERR'}

# --- THE NIFTY 500 SCANNER ---
def run_full_nifty500_scan(gap_threshold):
    url = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
    try:
        n500 = pd.read_csv(url)
        tickers = n500['Symbol'].tolist()
    except:
        st.error("Could not fetch Nifty 500 list.")
        return pd.DataFrame()

    scan_results = []
    progress_bar = st.progress(0)
    
    for i, sym in enumerate(tickers):
        if i % 25 == 0: progress_bar.progress(i / 500)
        try:
            t = yf.Ticker(f"{sym}.NS")
            hist = t.history(period="1y")
            if len(hist) > 200:
                sma200 = hist['Close'].rolling(window=200).mean().iloc[-1]
                prev_close = hist['Close'].iloc[-2]
                curr_open = hist['Open'].iloc[-1]
                ltp = hist['Close'].iloc[-1]
                
                gap = ((curr_open - prev_close) / prev_close) * 100
                day_change = ((ltp - prev_close) / prev_close) * 100
                
                # CRITERIA: Stage 2 + (Gap or High Day Change)
                if ltp > sma200 and (gap >= gap_threshold or day_change > gap_threshold):
                    avg_vol = hist['Volume'].tail(50).mean()
                    curr_vol = hist['Volume'].iloc[-1]
                    scan_results.append({
                        'Symbol': sym, 
                        'Gap %': round(gap, 2), 
                        'Day Change %': round(day_change, 2),
                        'Vol Multiplier': round(curr_vol / avg_vol, 2),
                        'Price': round(ltp, 2)
                    })
        except: continue
            
    progress_bar.empty()
    return pd.DataFrame(scan_results)

# --- TABS ---
tab1, tab2, tab3 = st.tabs(["🚀 Morning Scanner", "💰 Intraday Monitor", "📈 Swing Monitor"])

with tab1:
    st.header("Step 1: The 5% Gap Search")
    
    # Reset button to start over
    if st.button("🔄 Reset Scanner"):
        st.session_state.scan_stage = "idle"
        st.rerun()

    if st.session_state.scan_stage == "idle":
        if st.button("🎯 Start Primary 5% Scan"):
            with st.spinner("Scanning Nifty 500 for Institutional 5% Gaps..."):
                results = run_full_nifty500_scan(5.0)
                if results.empty:
                    st.session_state.scan_stage = "first_failed"
                    st.rerun()
                else:
                    st.session_state.scan_results = results
                    st.session_state.scan_stage = "results_found"
                    st.rerun()

    if st.session_state.scan_stage == "first_failed":
        st.warning("⚠️ WARNING: No candidates found with a 5% Gap + Stage 2 filter.")
        st.info("The market may be quiet. Do you want to proceed to the Next Stage (3.5% Relative Strength Scan)?")
        
        col1, col2 = st.columns(2)
        with col1:
            if st.button("✅ Yes, Scan for Next Stage (3.5%)"):
                with st.spinner("Scanning Nifty 500 for 3.5% Strength Candidates..."):
                    results = run_full_nifty500_scan(3.5)
                    st.session_state.scan_results = results
                    st.session_state.scan_stage = "results_found"
                    st.rerun()
        with col2:
            if st.button("❌ No, Stop for Today"):
                st.session_state.scan_stage = "idle"
                st.rerun()

    if st.session_state.scan_stage == "results_found":
        st.success(f"Scan Complete! Found {len(st.session_state.scan_results)} candidates.")
        st.dataframe(st.session_state.scan_results.sort_values(by='Vol Multiplier', ascending=False), use_container_width=True)

with tab2:
    st.header("Intraday (5x Margin) Monitor")
    file = st.sidebar.file_uploader("Upload Stoxkart P&L", type=['xlsx', 'csv'])
    if file:
        df_p = pd.read_csv(file) if file.name.endswith('.csv') else pd.read_excel(file)
        active = df_p[df_p['Open Qty'] > 0].copy()
        if not active.empty:
            mon = [get_live_stats(s) for s in active['Name']]
            live = pd.DataFrame(mon)
            final = pd.concat([active.reset_index(drop=True), live], axis=1)
            final['Status'] = final.apply(lambda x: "🚨 EXIT" if x['LTP'] < x['VWAP'] else "✅ HOLD", axis=1)
            st.table(final[['Name', 'Open Qty', 'LTP', 'VWAP', 'Status']])

with tab3:
    st.header("Swing (Cash) Monitor")
    try:
        df_s = conn.read(worksheet="SWING_PORTFOLIO")
        if not df_s.empty:
            stats = [get_live_stats(s) for s in df_s['Symbol']]
            live_s = pd.concat([df_s.reset_index(drop=True), pd.DataFrame(stats)], axis=1)
            live_s['SL'] = live_s['Entry_Price'] * 0.93
            live_s['Action'] = live_s.apply(lambda x: "🚨 SELL" if x['LTP'] < x['SL'] else "✅ OK", axis=1)
            st.table(live_s[['Symbol', 'Entry_Price', 'SL', 'LTP', 'Action']])
    except: st.info("Check SWING_PORTFOLIO sheet.")

st.caption(f"Refreshed: {datetime.now().strftime('%H:%M:%S')}")

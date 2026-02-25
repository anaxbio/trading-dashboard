import streamlit as st
import pandas as pd
import requests
import yfinance as yf
from streamlit_gsheets import GSheetsConnection
from datetime import datetime

# --- CONFIGURATION ---
st.set_page_config(page_title="EP Stage 2 Monitor", layout="wide")
st.title("🚀 EP Stage 2: Scanner & Exit Monitor")

# Connect to Google Sheets
try:
    conn = st.connection("gsheets", type=GSheetsConnection)
except:
    st.error("Google Sheets connection not found. Please check Secrets.")

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
def run_full_nifty500_scan():
    """Scans the Nifty 500 for Stage 2 + 5% Gap up."""
    # Step 1: Get Nifty 500 list from a reliable source
    url = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
    try:
        n500 = pd.read_csv(url)
        tickers = n500['Symbol'].tolist()
    except:
        st.error("Could not fetch Nifty 500 list from NSE.")
        return pd.DataFrame()

    scan_results = []
    progress_bar = st.progress(0)
    
    # Batch processing for speed (lite approach)
    for i, sym in enumerate(tickers):
        if i % 50 == 0: progress_bar.progress(i / 500) # Update progress every 50 stocks
        try:
            t = yf.Ticker(f"{sym}.NS")
            # We fetch 1y data to check SMA 200 (Stage 2 check)
            hist = t.history(period="1y")
            if len(hist) > 200:
                sma200 = hist['Close'].rolling(window=200).mean().iloc[-1]
                prev_close = hist['Close'].iloc[-2]
                curr_open = hist['Open'].iloc[-1] # Open price of today
                
                gap = ((curr_open - prev_close) / prev_close) * 100
                
                # THE CRITERIA: Stage 2 + 5% Gap
                if curr_open > sma200 and gap >= 5:
                    # Check Volume Shock (Today's Open Volume vs 50-day Avg)
                    avg_vol = hist['Volume'].tail(50).mean()
                    curr_vol = hist['Volume'].iloc[-1]
                    vol_shock = round(curr_vol / avg_vol, 2)
                    
                    scan_results.append({
                        'Symbol': sym, 
                        'Gap %': round(gap, 2), 
                        'Vol Multiplier': vol_shock,
                        'Price': round(curr_open, 2)
                    })
        except:
            continue
            
    progress_bar.empty()
    return pd.DataFrame(scan_results)

# --- SIDEBAR & TABS ---
st.sidebar.header("Broker Sync")
uploaded_file = st.sidebar.file_uploader("Upload Stoxkart Excel/CSV", type=['xlsx', 'csv'])

tab1, tab2, tab3 = st.tabs(["🚀 Morning Scanner", "💰 Intraday (5x)", "📈 Swing (Cash)"])

with tab1:
    st.header("What to Buy: EP + Stage 2 (Nifty 500)")
    if st.button("Start Full Market Scan"):
        with st.spinner("Analyzing all 500 stocks for EP signals..."):
            picks = run_full_nifty500_scan()
            if not picks.empty:
                st.success(f"Found {len(picks)} candidates!")
                st.table(picks.sort_values(by='Gap %', ascending=False))
            else:
                st.warning("No stocks currently meet the 5% Gap + Stage 2 criteria.")

with tab2:
    st.header("Intraday: 2m VWAP Monitor")
    if uploaded_file:
        df_pnl = pd.read_csv(uploaded_file) if uploaded_file.name.endswith('.csv') else pd.read_excel(uploaded_file)
        active = df_pnl[df_pnl['Open Qty'] > 0].copy()
        if not active.empty:
            mon_data = [get_live_stats(s) for s in active['Name']]
            live_df = pd.DataFrame(mon_data)
            final_df = pd.concat([active.reset_index(drop=True), live_df], axis=1)
            final_df['Status'] = final_df.apply(lambda x: "🚨 EXIT" if x['LTP'] < x['VWAP'] else "✅ HOLD", axis=1)
            st.table(final_df[['Name', 'Open Qty', 'LTP', 'VWAP', 'Status']])

with tab3:
    st.header("Swing: -7% Exit Monitor")
    try:
        df_swing = conn.read(worksheet="SWING_PORTFOLIO")
        if not df_swing.empty:
            results = [get_live_stats(s) for s in df_swing['Symbol']]
            live_s = pd.DataFrame(results)
            final_s = pd.concat([df_swing.reset_index(drop=True), live_s], axis=1)
            final_s['SL'] = final_s['Entry_Price'] * 0.93
            final_s['Action'] = final_s.apply(lambda x: "🚨 SELL" if x['LTP'] < x['SL'] else "✅ OK", axis=1)
            st.table(final_s[['Symbol', 'Entry_Price', 'SL', 'LTP', 'Action']])
    except:
        st.info("Check SWING_PORTFOLIO tab in Google Sheets.")

st.caption(f"Refreshed: {datetime.now().strftime('%H:%M:%S')} | No Laptop Access Required.")

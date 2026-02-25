import streamlit as st
import pandas as pd
import requests
import yfinance as yf
from streamlit_gsheets import GSheetsConnection
from datetime import datetime

# --- CONFIGURATION ---
st.set_page_config(page_title="EP Stage 2 Monitor", layout="wide")
st.title("🚀 EP Stage 2: Scanner & Exit Monitor")

# Connect to Google Sheets (Defined in Streamlit Secrets)
try:
    conn = st.connection("gsheets", type=GSheetsConnection)
except:
    st.error("Google Sheets connection not found. Please check your Secrets configuration.")

# --- DATA FETCHING ENGINE ---
def get_live_stats(symbol):
    """Hits Moneycontrol JSON for Price/VWAP, falls back to YFinance."""
    clean_sym = str(symbol).split('-')[0].strip() # Clean Stoxkart suffix if any
    try:
        # Primary: Moneycontrol
        url = f"https://priceapi.moneycontrol.com/pricefeed/nse/equityinst/{clean_sym}"
        res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5).json()
        if res['msg'] == 'success':
            return {
                'LTP': float(res['data']['lastPrice']),
                'VWAP': float(res['data']['averagePrice']),
                'Src': 'MC'
            }
    except:
        pass
    
    try:
        # Secondary: YFinance
        t = yf.Ticker(f"{clean_sym}.NS")
        df = t.history(period="1d", interval="2m")
        if not df.empty:
            ltp = df['Close'].iloc[-1]
            vwap = (df['Close'] * df['Volume']).sum() / df['Volume'].sum()
            return {'LTP': round(ltp, 2), 'VWAP': round(vwap, 2), 'Src': 'YF'}
    except:
        return {'LTP': 0, 'VWAP': 0, 'Src': 'ERR'}

# --- THE SCANNER LOGIC ---
def run_morning_scan():
    """Simplified Nifty 500 Scanner for EP and Stage 2."""
    # To keep it lite, we fetch Nifty 500 list from a public gist or common source
    # For now, we scan a 'Watchlist' sheet or top liquid stocks
    watchlist = ["RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "BHARTIARTL", "SBIN", "LICI", "ITC", "HUL"]
    
    scan_results = []
    for sym in watchlist:
        try:
            t = yf.Ticker(f"{sym}.NS")
            hist = t.history(period="1y")
            if len(hist) > 200:
                sma200 = hist['Close'].rolling(window=200).mean().iloc[-1]
                prev_close = hist['Close'].iloc[-2]
                curr_open = t.fast_info['last_price'] # Simple check
                gap = ((curr_open - prev_close) / prev_close) * 100
                
                if curr_open > sma200 and gap > 5:
                    scan_results.append({'Symbol': sym, 'Gap%': round(gap, 2), 'Above_200SMA': '✅'})
        except:
            continue
    return pd.DataFrame(scan_results)

# --- SIDEBAR: SYNC BROKER ---
st.sidebar.header("Broker Sync")
uploaded_file = st.sidebar.file_uploader("Upload Stoxkart Excel/CSV", type=['xlsx', 'csv'])

# --- TABS INTERFACE ---
tab1, tab2, tab3 = st.tabs(["🚀 Morning Scanner", "💰 Intraday (5x)", "📈 Swing (Cash)"])

with tab1:
    st.header("What to Buy: EP + Stage 2 Picks")
    if st.button("Run Daily Scan (Nifty 500 Universe)"):
        with st.spinner("Analyzing Stage 2 Trends & Morning Gaps..."):
            picks = run_morning_scan()
            if not picks.empty:
                st.success("Potential EP Candidates Found:")
                st.table(picks)
            else:
                st.warning("No stocks met the 5% Gap + Stage 2 criteria yet.")

with tab2:
    st.header("Intraday: 2m VWAP Monitor")
    if uploaded_file:
        df_pnl = pd.read_csv(uploaded_file) if uploaded_file.name.endswith('.csv') else pd.read_excel(uploaded_file)
        # Filter for open intraday positions (logic: if Net Qty > 0)
        # Adjusting to your Stoxkart P&L headers
        active = df_pnl[df_pnl['Open Qty'] > 0].copy()
        
        if not active.empty:
            mon_data = []
            for s in active['Name']: # 'Name' column from your file
                stats = get_live_stats(s)
                mon_data.append(stats)
            
            live_df = pd.DataFrame(mon_data)
            final_df = pd.concat([active.reset_index(drop=True), live_df], axis=1)
            
            final_df['Status'] = final_df.apply(lambda x: "🚨 EXIT" if x['LTP'] < x['VWAP'] else "✅ HOLD", axis=1)
            st.table(final_df[['Name', 'Open Qty', 'LTP', 'VWAP', 'Status']])
        else:
            st.info("Upload your Excel to track live Intraday exits.")

with tab3:
    st.header("Swing: -7% Exit Monitor")
    # This reads from your Google Sheet 'SWING_PORTFOLIO'
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

st.caption(f"Last Pulse: {datetime.now().strftime('%H:%M:%S')} | No Laptop Access Required.")

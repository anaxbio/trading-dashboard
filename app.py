import streamlit as st
import pandas as pd
import requests
import yfinance as yf
from streamlit_gsheets import GSheetsConnection
from datetime import datetime

st.set_page_config(page_title="EP Monitor", layout="wide")
st.title("📈 EP Live Exit Monitor")

# --- DATA FETCHING (MONEYCONTROL PRIMARY) ---
def get_live_stats(symbol):
    """Hits MC JSON for Price/VWAP, falls back to YF if needed."""
    try:
        # Standard MC JSON Fetch (Primary)
        url = f"https://priceapi.moneycontrol.com/pricefeed/nse/equityinst/{symbol}"
        res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5).json()
        if res['msg'] == 'success':
            return {
                'LTP': float(res['data']['lastPrice']),
                'VWAP': float(res['data']['averagePrice']),
                'Src': 'MC'
            }
    except:
        pass
    
    # Fallback to YFinance (Secondary)
    try:
        t = yf.Ticker(f"{symbol}.NS")
        df = t.history(period="1d", interval="2m")
        if not df.empty:
            ltp = df['Close'].iloc[-1]
            vwap = (df['Close'] * df['Volume']).sum() / df['Volume'].sum()
            return {'LTP': round(ltp, 2), 'VWAP': round(vwap, 2), 'Src': 'YF'}
    except:
        return {'LTP': 0, 'VWAP': 0, 'Src': 'ERR'}

# --- UI: FILE UPLOAD ---
st.sidebar.header("Sync Broker Data")
file = st.sidebar.file_uploader("Upload Stoxkart P&L", type=['csv', 'xlsx'])

if file:
    df = pd.read_csv(file) if file.name.endswith('.csv') else pd.read_excel(file)
    
    # FILTER: Only show active trades (where you haven't sold everything yet)
    # Stoxkart P&L usually has 'Quantity' or 'Net Qty'
    active_trades = df[df['Realized P&L'].isnull() | (df['Realized P&L'] == 0)].copy()

    if not active_trades.empty:
        st.subheader("⚠️ Active Risk Monitor")
        
        # Pull Live Data
        monitor_list = []
        for sym in active_trades['Symbol']:
            stats = get_live_stats(sym)
            monitor_list.append(stats)
        
        live_df = pd.DataFrame(monitor_list)
        final_df = pd.concat([active_trades.reset_index(drop=True), live_df], axis=1)
        
        # --- THE EXIT DECIDER ---
        def decide(row):
            # Intraday Check (If 5x margin assumed)
            if row['LTP'] < row['VWAP']:
                return "🚨 EXIT (VWAP Break)"
            # Swing Check (7% Stop)
            if row['LTP'] < (row['Buy Average'] * 0.93):
                return "🚨 SELL (SL Hit)"
            return "✅ HOLD"

        final_df['Action'] = final_df.apply(decide, axis=1)
        
        # Display the "Lite" Table
        st.table(final_df[['Symbol', 'Buy Average', 'LTP', 'VWAP', 'Action', 'Src']])
    else:
        st.success("All trades closed. No active risk.")

st.caption(f"Refreshed: {datetime.now().strftime('%H:%M:%S')} | Logic: 2m Candle Close Buffer")

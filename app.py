import streamlit as st
import pandas as pd
import requests
import yfinance as yf
from streamlit_gsheets import GSheetsConnection
from datetime import datetime

st.set_page_config(page_title="EP Dashboard", layout="wide")
st.title("🚀 EP Stage 2 Tracker (MC Primary)")

# --- CONFIG ---
# Note: In your Google Sheet, add a 'MC_ID' column if you have it. 
# If not, we fallback to YFinance or a basic scraper.
conn = st.connection("gsheets", type=GSheetsConnection)

def get_mc_price(symbol):
    """Hits Moneycontrol's JSON endpoint for live price & VWAP."""
    try:
        # This is a common internal MC endpoint format
        # We use a headers-spoof to ensure we aren't blocked
        url = f"https://priceapi.moneycontrol.com/pricefeed/nse/equityinst/{symbol}"
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=5)
        data = response.json()
        
        if data['msg'] == 'success':
            price_data = data['data']
            return {
                'LTP': float(price_data['lastPrice']),
                'VWAP': float(price_data['averagePrice']), # MC provides ATP/VWAP
                'Source': 'Moneycontrol'
            }
    except Exception:
        # Fallback to YFinance if MC fails or Symbol is different
        ticker = yf.Ticker(f"{symbol}.NS")
        df = ticker.history(period="1d", interval="2m")
        if not df.empty:
            ltp = df['Close'].iloc[-1]
            vwap = (df['Close'] * df['Volume']).sum() / df['Volume'].sum()
            return {'LTP': round(ltp, 2), 'VWAP': round(vwap, 2), 'Source': 'YFinance (Fallback)'}
    return {'LTP': 0, 'VWAP': 0, 'Source': 'Error'}

# --- UPLOAD SECTION ---
st.sidebar.header("Upload Stoxkart P&L")
uploaded_file = st.sidebar.file_uploader("Upload Excel", type=['xlsx'])

# --- MONITORING LOGIC ---
tab1, tab2 = st.tabs(["💰 Intraday (5x)", "📈 Swing (Cash)"])

with tab1:
    st.header("Intraday: 2m VWAP Pulse")
    df_intra = conn.read(worksheet="INTRADAY_PORTFOLIO")
    if not df_intra.empty:
        results = []
        for sym in df_intra['Symbol']:
            results.append(get_mc_price(sym))
        
        df_live = pd.DataFrame(results)
        df_final = pd.concat([df_intra.reset_index(drop=True), df_live], axis=1)
        
        # 2-Min Buffer Logic
        df_final['Status'] = df_final.apply(lambda x: "🚨 EXIT" if x['LTP'] < x['VWAP'] else "✅ HOLD", axis=1)
        st.table(df_final[['Symbol', 'Entry_Price', 'LTP', 'VWAP', 'Status', 'Source']])

with tab2:
    st.header("Swing: -7% Hard Stop")
    df_swing = conn.read(worksheet="SWING_PORTFOLIO")
    if not df_swing.empty:
        # Similar fetch logic for Swing
        results_s = [get_mc_price(s) for s in df_swing['Symbol']]
        df_live_s = pd.DataFrame(results_s)
        df_final_s = pd.concat([df_swing.reset_index(drop=True), df_live_s], axis=1)
        
        df_final_s['SL_Price'] = df_final_s['Entry_Price'] * 0.93
        df_final_s['Status'] = df_final_s.apply(lambda x: "🚨 SELL" if x['LTP'] < x['SL_Price'] else "✅ OK", axis=1)
        st.table(df_final_s[['Symbol', 'Entry_Price', 'SL_Price', 'LTP', 'Status']])

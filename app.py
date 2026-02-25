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
    """Self-healing price fetcher: Cleans symbols like RELIANCE.NS automatically."""
    clean_sym = str(symbol).split('.')[0].split('-')[0].strip().upper()
    try:
        url = f"https://priceapi.moneycontrol.com/pricefeed/nse/equityinst/{clean_sym}"
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, headers=headers, timeout=5).json()
        
        if res.get('msg') == 'success' and 'data' in res:
            return {
                'LTP': float(res['data']['lastPrice']), 
                'VWAP': float(res['data']['averagePrice'])
            }
    except:
        pass
    return {'LTP': 0, 'VWAP': 0}

def run_scan(threshold):
    """Fetches Nifty 500 and scans for Stage 2 breakouts."""
    url = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
    try:
        tickers = pd.read_csv(url)['Symbol'].tolist()
    except:
        return pd.DataFrame()
    
    results = []
    prog = st.progress(0)
    # Scanning first 120 stocks for speed on mobile
    for i, sym in enumerate(tickers[:120]):
        prog.progress(i / 120)
        try:
            t = yf.

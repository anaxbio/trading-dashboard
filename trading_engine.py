# trading_engine.py
import pandas as pd
import yfinance as yf
import numpy as np
import pandas_ta as ta
import requests
import io
from datetime import datetime
import pytz
from concurrent.futures import ThreadPoolExecutor
import streamlit as st # Only imported for caching and progress bars

# --- UTILS ---
def get_now_ist():
    return datetime.now(pytz.timezone('Asia/Kolkata'))

def is_market_open():
    now = get_now_ist()
    if now.weekday() >= 5: return False
    mkt_start = now.replace(hour=9, minute=15, second=0, microsecond=0)
    mkt_end = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return mkt_start <= now <= mkt_end

# --- INTRADAY & SWING ENGINES ---
def get_vwap_data(sym):
    try:
        t = yf.Ticker(f"{sym}.NS")
        df = t.history(period="1d", interval="1m")
        if df.empty or df['Volume'].sum() == 0: return 0.0, 0.0, 0.0
        df['TP'] = (df['High'] + df['Low'] + df['Close']) / 3
        vwap = (df['TP'] * df['Volume']).sum() / df['Volume'].sum()
        return round(df['Close'].iloc[-1], 2), round(vwap, 2), round(df['High'].max(), 2)
    except: return 0.0, 0.0, 0.0

def get_swing_stops(sym):
    try:
        t = yf.Ticker(f"{sym}.NS")
        hist = t.history(period="50d")
        if hist.empty: return 0.0, 0.0, 0.0
        return round(hist['Low'].iloc[-1], 2), round(hist['Close'].rolling(20).mean().iloc[-1], 2), round(hist['Close'].iloc[-1], 2)
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
        max_chg = ((hist['High'].iloc[-1] - prev_c) / prev_c) * 100
        avg_vol = hist['Volume'].tail(30).mean()
        rvol = hist['Volume'].iloc[-1] / (avg_vol if avg_vol > 0 else 1)
        
        if max_chg >= threshold and rvol > 1.2:
            _, vwap, _ = get_vwap_data(sym)
            if vwap == 0.0: vwap = curr_p 
            if curr_p < vwap: return None 
            return {
                'Symbol': sym, 'LTP': round(curr_p, 2), 'Max%': round(max_chg, 2), 
                'RVOL': round(rvol, 1), 'Dist_Wall%': round(((curr_p - sma200) / sma200) * 100, 2), 
                'Sys_SL': round(vwap - 2.0, 2)
            }
    except: pass
    return None

def run_engine(threshold, use_sma_wall, universe="Nifty 500"):
    urls = ["https://archives.nseindia.com/content/indices/ind_nifty500list.csv", "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv"] if universe == "Nifty 500" else ["https://archives.nseindia.com/content/indices/ind_niftymicrocap250list.csv"]
    tickers = []
    headers = {'User-Agent': 'Mozilla/5.0'}
    for url in urls:
        try:
            r = requests.get(url, headers=headers, timeout=5)
            if r.status_code == 200:
                tickers = pd.read_csv(io.StringIO(r.text))['Symbol'].tolist()
                break
        except: continue
    if not tickers: return pd.DataFrame()
    
    results = []
    prog = st.progress(0, text=f"Scanning {universe}...")
    with ThreadPoolExecutor(max_workers=25) as executor:
        futures = [executor.submit(process_ticker, s, threshold, use_sma_wall) for s in tickers]
        for i, f in enumerate(futures):
            prog.progress((i+1)/len(tickers), text=f"Scanning {universe}... {i+1}/{len(tickers)}")
            res = f.result()
            if res: results.append(res)
    prog.empty()
    
    df = pd.DataFrame(results)
    if not df.empty:
        sort_col = 'Dist_Wall%' if use_sma_wall else 'Max%'
        df = df.sort_values(by=sort_col, ascending=False).reset_index(drop=True)
        df['Rank'] = "Laggard"
        df.loc[0:4, 'Rank'] = "🔥 LEADER"
        return df.head(8)
    return df

# --- ETF ENGINE ---
@st.cache_data(ttl=86400)
def fetch_etf_universe():
    base_tickers = ["SILVERBEES", "GOLDBEES", "PSUBNKBEES", "MON100", "MID150BEES", "LIQUIDBEES"] # Truncated for brevity, paste your full list here
    try:
        url = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
        headers = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(url, headers=headers, timeout=5)
        if r.status_code == 200:
            df_nse = pd.read_csv(io.StringIO(r.text))
            new_etfs = df_nse[df_nse['NAME OF COMPANY'].str.contains('ETF|BEES|FUND', case=False, na=False)]['SYMBOL'].tolist()
            base_tickers.extend(new_etfs)
    except: pass
    return list(set(base_tickers))

def categorize_etf(sym):
    s = sym.upper()
    if 'SILV' in s: return 'SILVER'
    if 'GOLD' in s: return 'GOLD'
    if 'LIQ' in s or 'GSEC' in s: return 'LIQUID/DEBT'
    if 'MON' in s or 'FANG' in s: return 'INTERNATIONAL'
    if 'MOM' in s: return 'MOMENTUM'
    if 'IT' in s: return 'IT'
    if 'BANK' in s: return 'BANKING'
    return 'OTHER'

# --- SILENT SIGNAL ENGINE ---
def calc_silent_signal(sym, interval="15m", period="60d"):
    try:
        t = yf.Ticker(sym)
        df = t.history(period=period, interval=interval)
        if df.empty or len(df) < 200: return None
        
        df['EMA200'] = ta.ema(df['Close'], length=200)
        adx_df = ta.adx(df['High'], df['Low'], df['Close'], length=14)
        df['ADX'] = adx_df['ADX_14'] if adx_df is not None else 0

        prd = 3
        df['PH'] = np.nan; df['PL'] = np.nan
        for i in range(prd, len(df) - prd):
            if all(df['High'].iloc[i] >= df['High'].iloc[i-prd:i]) and all(df['High'].iloc[i] > df['High'].iloc[i+1:i+prd+1]):
                df.at[df.index[i], 'PH'] = df['High'].iloc[i]
            if all(df['Low'].iloc[i] <= df['Low'].iloc[i-prd:i]) and all(df['Low'].iloc[i] < df['Low'].iloc[i+1:i+prd+1]):
                df.at[df.index[i], 'PL'] = df['Low'].iloc[i]

        df['Center'] = np.nan
        curr_center = np.nan
        for i in range(len(df)):
            ph, pl = df['PH'].iloc[i], df['PL'].iloc[i]
            lastpp = ph if not np.isnan(ph) else (pl if not np.isnan(pl) else np.nan)
            if not np.isnan(lastpp): curr_center = lastpp if np.isnan(curr_center) else (curr_center * 2 + lastpp) / 3
            df.at[df.index[i], 'Center'] = curr_center

        df['atr'] = ta.atr(df['High'], df['Low'], df['Close'], length=3)
        df['Up'] = df['Center'] - (10.0 * df['atr'])
        df['Dn'] = df['Center'] + (10.0 * df['atr'])

        tup = np.zeros(len(df)); tdown = np.zeros(len(df)); trend = np.ones(len(df))
        for i in range(1, len(df)):
            tup[i] = max(df['Up'].iloc[i], tup[i-1]) if df['Close'].iloc[i-1] > tup[i-1] else df['Up'].iloc[i]
            tdown[i] = min(df['Dn'].iloc[i], tdown[i-1]) if df['Close'].iloc[i-1] < tdown[i-1] else df['Dn'].iloc[i]
            if df['Close'].iloc[i] > tdown[i-1]: trend[i] = 1
            elif df['Close'].iloc[i] < tup[i-1]: trend[i] = -1
            else: trend[i] = trend[i-1]

        df['Trend'] = trend
        df['TrailSL'] = np.where(df['Trend'] == 1, tup, tdown)
        
        last = df.iloc[-1]; prev = df.iloc[-2]
        is_trending = last['ADX'] > 20
        is_aligned = (last['Trend'] == 1 and last['Close'] > last['EMA200']) or (last['Trend'] == -1 and last['Close'] < last['EMA200'])
        
        signal = "WAIT/CHOP"
        if last['Trend'] == 1 and prev['Trend'] == -1 and is_trending and is_aligned: signal = "🟢 NEW BUY"
        elif last['Trend'] == -1 and prev['Trend'] == 1 and is_trending and is_aligned: signal = "🔴 NEW SELL"
        elif is_aligned: signal = "HOLDING"

        return {
            "Symbol": sym.replace(".NS", ""), "LTP": round(last['Close'], 2),
            "Trend": "BULL 🟢" if last['Trend'] == 1 else "BEAR 🔴",
            "StopLoss": round(last['TrailSL'], 2), "ADX": round(last['ADX'], 1),
            "Regime": "Trending" if is_trending else "CHOP", "Signal": signal
        }
    except: return None

import streamlit as st
import pandas as pd
import yfinance as yf
from streamlit_gsheets import GSheetsConnection
from datetime import datetime
import time
import pytz
from concurrent.futures import ThreadPoolExecutor
import requests
import io
import numpy as np
import pandas_ta as ta

# --- CONFIG & SETUP ---
st.set_page_config(page_title="EP Dual-Engine Cockpit", layout="wide")
st.title("🛡️ EP Strategy: Multi-Asset War Room")

def get_now_ist():
    return datetime.now(pytz.timezone('Asia/Kolkata'))

def is_market_open():
    now = get_now_ist()
    if now.weekday() >= 5: return False
    mkt_start = now.replace(hour=9, minute=15, second=0, microsecond=0)
    mkt_end = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return mkt_start <= now <= mkt_end

# Connect to Google Sheets
conn = st.connection("gsheets", type=GSheetsConnection)

# --- DATA ENGINES ---

def calc_silent_signal(sym, interval="15m", period="60d"):
    """
    Direct Python translation of the SilentSignal Pine Script.
    Calculates Pivot-based Supertrend with ADX/EMA Anti-Chop filters.
    """
    try:
        t = yf.Ticker(sym)
        df = t.history(period=period, interval=interval)
        if df.empty or len(df) < 200: return None
        
        # 1. Chop Filters (ADX & EMA)
        df['EMA200'] = ta.ema(df['Close'], length=200)
        adx_df = ta.adx(df['High'], df['Low'], df['Close'], length=14)
        df['ADX'] = adx_df['ADX_14'] if adx_df is not None else 0

        # 2. Pivot Point Calculations (prd=3)
        prd = 3
        df['PH'] = np.nan
        df['PL'] = np.nan
        
        for i in range(prd, len(df) - prd):
            # Pivot High
            if all(df['High'].iloc[i] >= df['High'].iloc[i-prd:i]) and \
               all(df['High'].iloc[i] > df['High'].iloc[i+1:i+prd+1]):
                df.at[df.index[i], 'PH'] = df['High'].iloc[i]
            # Pivot Low
            if all(df['Low'].iloc[i] <= df['Low'].iloc[i-prd:i]) and \
               all(df['Low'].iloc[i] < df['Low'].iloc[i+1:i+prd+1]):
                df.at[df.index[i], 'PL'] = df['Low'].iloc[i]

        # 3. Center Line Calculation
        df['Center'] = np.nan
        curr_center = np.nan
        for i in range(len(df)):
            ph, pl = df['PH'].iloc[i], df['PL'].iloc[i]
            lastpp = ph if not np.isnan(ph) else (pl if not np.isnan(pl) else np.nan)
            if not np.isnan(lastpp):
                curr_center = lastpp if np.isnan(curr_center) else (curr_center * 2 + lastpp) / 3
            df.at[df.index[i], 'Center'] = curr_center

        # 4. ATR Bands (Factor 10.0, Pd 3)
        factor = 10.0
        df['atr'] = ta.atr(df['High'], df['Low'], df['Close'], length=3)
        df['Up'] = df['Center'] - (factor * df['atr'])
        df['Dn'] = df['Center'] + (factor * df['atr'])

        # 5. Trailing Trend Logic (Recursive Step)
        tup = np.zeros(len(df))
        tdown = np.zeros(len(df))
        trend = np.ones(len(df))
        
        for i in range(1, len(df)):
            # Up Band Trailing
            if df['Close'].iloc[i-1] > tup[i-1]:
                tup[i] = max(df['Up'].iloc[i], tup[i-1])
            else:
                tup[i] = df['Up'].iloc[i]
            
            # Down Band Trailing
            if df['Close'].iloc[i-1] < tdown[i-1]:
                tdown[i] = min(df['Dn'].iloc[i], tdown[i-1])
            else:
                tdown[i] = df['Dn'].iloc[i]
            
            # Trend Flip Logic
            if df['Close'].iloc[i] > tdown[i-1]:
                trend[i] = 1
            elif df['Close'].iloc[i] < tup[i-1]:
                trend[i] = -1
            else:
                trend[i] = trend[i-1]

        df['Trend'] = trend
        df['TrailSL'] = np.where(df['Trend'] == 1, tup, tdown)
        
        last = df.iloc[-1]
        prev = df.iloc[-2]
        
        # 6. Signal Integration with Anti-Chop
        is_trending = last['ADX'] > 20
        is_aligned = (last['Trend'] == 1 and last['Close'] > last['EMA200']) or \
                     (last['Trend'] == -1 and last['Close'] < last['EMA200'])
        
        signal = "WAIT/CHOP"
        if last['Trend'] == 1 and prev['Trend'] == -1 and is_trending and is_aligned:
            signal = "🟢 NEW BUY"
        elif last['Trend'] == -1 and prev['Trend'] == 1 and is_trending and is_aligned:
            signal = "🔴 NEW SELL"
        elif is_aligned:
            signal = "HOLDING"

        return {
            "Symbol": sym.replace(".NS", ""),
            "LTP": round(last['Close'], 2),
            "Trend": "BULL 🟢" if last['Trend'] == 1 else "BEAR 🔴",
            "StopLoss": round(last['TrailSL'], 2),
            "ADX": round(last['ADX'], 1),
            "Regime": "Trending" if is_trending else "CHOP",
            "Signal": signal
        }
    except: return None

# (Keep existing get_vwap_data, get_swing_stops, process_ticker, fetch_etf_universe)
def get_vwap_data(sym):
    try:
        t = yf.Ticker(f"{sym}.NS")
        df = t.history(period="1d", interval="1m")
        if df.empty or df['Volume'].sum() == 0: return 0.0, 0.0, 0.0
        df['TP'] = (df['High'] + df['Low'] + df['Close']) / 3
        vol_sum = df['Volume'].sum()
        vwap = (df['TP'] * df['Volume']).sum() / vol_sum
        ltp = df['Close'].iloc[-1]
        hod = df['High'].max() 
        return round(ltp, 2), round(vwap, 2), round(hod, 2)
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
            _, vwap, _ = get_vwap_data(sym)
            if vwap == 0.0: vwap = curr_p 
            if curr_p < vwap: return None 
            sys_sl = round(vwap - 2.0, 2)
            dist_wall = round(((curr_p - sma200) / sma200) * 100, 2)
            return {'Symbol': sym, 'LTP': round(curr_p, 2), 'Max%': round(max_chg, 2), 'RVOL': round(rvol, 1), 'Dist_Wall%': dist_wall, 'Sys_SL': sys_sl}
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
        df['Rank'] = "Laggard"; df.loc[0:4, 'Rank'] = "🔥 LEADER"; df = df.head(8)
    return df

@st.cache_data(ttl=86400)
def fetch_etf_universe():
    base_tickers = ["SILVERADD", "SILVERIETF", "TATSILV", "AXISILVER", "HDFCSILVER", "SILVERBEES", "SILVER1", "SILVER", "SBISILVER", "ESILVER", "AXISGOLD", "UNIONGOLD", "QGOLDHALF", "LICMFGOLD", "GOLDIETF", "GOLDCASE", "GOLD1", "BSLGOLDETF", "HDFCGOLD", "GOLDBEES", "BBNPPGOLD", "SETFGOLD", "IVZINGOLD", "EGOLD", "TATAGOLD", "GOLDETF", "BANKPSU", "PSUBANKADD", "PSUBANK", "HDFCPSUBK", "PSUBNKIETF", "PSUBNKBEES", "METAL", "METALIETF", "VAL30IETF", "MOVALUE", "GROWWGOLD", "ICICIB22", "CPSEETF", "HNGSNGBEES", "COMMOIETF", "MONQ50", "MODEFENCE", "AUTOIETF", "AUTOBEES", "MASPTOP50", "ABSLPSE", "MON100", "MAKEINDIA", "MNC", "EBANKNIFTY", "BBNPNBETF", "ABSLBANETF", "SETFNIFBK", "BANKIETF", "ECAPINSURE", "BANKNIFTY1", "BANKBETF", "BANKETF", "BANKBEES", "HDFCNIFBAN", "NEXT30ADD", "AXISBNKETF", "OILIETF", "FINIETF", "BFSI", "PHARMABEES", "EQUAL50ADD", "SBINEQWETF", "HEALTHIETF", "ABSLNN50ET", "AXISHCETF", "HEALTHADD", "DIVOPPBEES", "HDFCPVTBAN", "INFRAIETF", "NEXT50", "HEALTHY", "SBIETFPB", "PVTBANKADD", "HDFCNEXT50", "SETFNN50", "JUNIORBEES", "INFRABEES", "NPBET", "NEXT50IETF", "PVTBANIETF", "MIDCAPIETF", "MIDCAP", "MID150CASE", "HDFCMID150", "HDFCBSE500", "MIDCAPETF", "MID150BEES", "MAFANG", "ALPHAETF", "MOM100", "ALPL30IETF", "GSEC5IETF", "EVINDIA", "GROWWEV", "MOHEALTH", "SDL26BEES", "MOMENTUM", "MOM30IETF", "HDFCMOMENT", "HDFCLOWVOL", "AXISBPSETF", "EBBETF0430", "GILT5YBEES", "NIF100IETF", "HDFCNIF100", "MOMOMENTUM", "GSEC10YEAR", "LOWVOL", "BBETF0432", "LOWVOLIETF", "NIF100BEES", "EBBETF0431", "LICNMID100", "LIQUID1", "LIQUIDPLUS", "TOP100CASE", "LIQUIDCASE", "LIQUIDADD", "ABGSEC", "MSCIINDIA", "LIQUIDBETF", "LICNETFGSC", "IVZINNIFTY", "BSLNIFTY", "LICNETFN50", "BSE500IETF", "LIQUIDSHRI", "GROWWLIQID", "NETF", "HDFCLIQUID", "NIFTYBETF", "EBBETF0433", "NIFTY1", "LTGILTBEES", "MOLOWVOL", "NIFTYBEES", "NIFTYETF", "QNIFTY", "AXISNIFTY", "NIFTYIETF", "SETFNIF50", "MOGSEC", "HDFCNIFTY", "MOM50", "IDFNIFTYET", "ALPHA", "LOWVOL1", "GROWWDEFNC", "MULTICAP", "MONIFTY500", "HDFCVALUE", "MIDSELIETF", "GSEC10IETF", "NV20BEES", "NV20IETF", "SETF10GILT", "NV20", "GSEC10ABSL", "MOMENTUM50", "LICNFNHGP", "ESG", "AXSENSEX", "BSLSENETFG", "SENSEXIETF", "SENSEXETF", "HDFCSENSEX", "SENSEXADD", "LICNETFSEN", "HDFCQUAL", "EMULTIMQ", "MOCAPITAL", "MIDQ50ADD", "NIFTYQLITY", "NIFTY100EW", "QUAL30IETF", "LIQUIDETF", "LIQUID", "LIQUIDIETF", "LIQUIDSBI", "LIQUIDBEES", "ABSLLIQUID", "SBIETFQLTY", "MIDSMALL", "HDFCGROWTH", "GROWWN200", "CONSUMIETF", "CONSUMBEES", "SBIETFCON", "CONS", "AXISCETF", "AONETOTAL", "TOP10ADD", "MOSMALL250", "HDFCSML250", "MAHKTECH", "CONSUMER", "SMALLCAP", "SHARIABEES", "FMCGIETF", "GROWWRAIL", "TNIDETF", "MOREALTY", "TECH", "ITIETF", "AXISTECETF", "ITETF", "ITBEES", "SBIETFIT", "HDFCNIFIT", "IT", "MOQUALITY", "SILVERETF", "GOLDSHARE", "GOLDETFADD", "UTIBANKETF", "BANKETFADD", "UTINIFTETF", "NIFTY50ADD", "UTISENSETF", "NIFMID150", "UTISXN50", "NIF5GETF", "NIF10GETF", "UTINEXT50", "NIFITETF", "ITETFADD", "SILVRETF", "EBBETF0425"]
    try:
        url = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
        headers = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(url, headers=headers, timeout=5)
        if r.status_code == 200:
            df_nse = pd.read_csv(io.StringIO(r.text))
            is_etf = df_nse['NAME OF COMPANY'].str.contains('ETF|BEES|FUND', case=False, na=False)
            new_etfs = df_nse[is_etf]['SYMBOL'].tolist()
            base_tickers.extend(new_etfs)
    except: pass
    return list(set(base_tickers))

# --- UI TABS ---
tab1, tab2, tab3, tab4 = st.tabs(["🚀 INTRADAY 5X", "📈 STAGE 2 SWING", "🛡️ ETF ALIGNER", "🔭 SILENT SIGNAL"])

# (Keep existing tab1, tab2, tab3 code logic)
with tab1:
    st.subheader("Step 1: Intraday Hunter")
    col_cap, col_info = st.columns([2, 1])
    with col_cap: intra_capital = st.slider("Total Buying Power (₹) [Incl. 5X Leverage]", 10000, 1000000, 100000, 10000)
    with col_info: st.metric("Required Cash Margin", f"₹{int(intra_capital / 5):,}")
    if st.button("🔥 Scan Intraday Movers"): st.session_state.intra_results = run_engine(4.0, use_sma_wall=False)
    if 'intra_results' in st.session_state:
        if not st.session_state.intra_results.empty:
            df_i = st.session_state.intra_results.copy()
            df_i['Qty'] = (intra_capital / df_i['LTP']).astype(int)
            df_i['Max_Loss (₹)'] = ((df_i['LTP'] - df_i['Sys_SL']) * df_i['Qty']).round(2)
            st.dataframe(df_i[['Rank', 'Symbol', 'LTP', 'Max_Loss (₹)', 'Sys_SL', 'Qty']], hide_index=True)
            with st.form("intra_commit"):
                confirmed = []
                for _, r in df_i.iterrows():
                    if r['Rank'] == "🔥 LEADER":
                        if st.checkbox(f"Trade {r['Symbol']} (Qty: {r['Qty']} | Risk: ₹{r['Max_Loss (₹)']})", key=f"intra_{r['Symbol']}"):
                            confirmed.append({'Symbol': r['Symbol'], 'Entry_Price': r['LTP'], 'Qty': r['Qty'], 'Date': get_now_ist().strftime('%Y-%m-%d %H:%M'), 'Status': 'OPEN'})
                if st.form_submit_button("💾 COMMIT TO WAR ROOM"):
                    df_cur = conn.read(worksheet="INTRADAY_PORTFOLIO", ttl=0).dropna(how='all')
                    conn.update(worksheet="INTRADAY_PORTFOLIO", data=pd.concat([df_cur, pd.DataFrame(confirmed)], ignore_index=True))
                    st.success("Committed!"); time.sleep(1); st.rerun()
        else: st.warning("🚨 0 stocks passed filter.")
    st.write("---")
    st.subheader("🛰️ Active War Room")
    @st.fragment(run_every="120s")
    def live_intra():
        if not is_market_open(): st.info("😴 Market Closed.")
        try:
            df = conn.read(worksheet="INTRADAY_PORTFOLIO", ttl=0).dropna(how='all')
            active = df[df['Status'].astype(str).str.upper() == 'OPEN'].copy()
            if active.empty: return st.write("No active trades.")
            with st.expander("📝 Manage Trades"):
                with st.form("edit_intra"):
                    upd = []
                    for idx, r in active.iterrows():
                        c1, c2, c3, c4, c5 = st.columns([1.5, 1, 1.2, 1.2, 1.5])
                        c1.markdown(f"**{r['Symbol']}**")
                        q = c2.number_input("Qty", value=int(float(r['Qty'])), key=f"iq_{idx}")
                        p = c3.number_input("Buy", value=float(r['Entry_Price']), key=f"ip_{idx}")
                        ex = c4.number_input("Exit", value=0.00, key=f"ep_{idx}")
                        s = c5.selectbox("Action", ["HOLD", "CLOSE TRADE"], key=f"ist_{idx}")
                        upd.append({'idx': idx, 'q': q, 'p': p, 'ep': ex, 's': s})
                    if st.form_submit_button("✅ Update"):
                        for u in upd:
                            df.at[u['idx'], 'Qty'] = u['q']; df.at[u['idx'], 'Entry_Price'] = u['p']
                            if u['s'] == "CLOSE TRADE": df.at[u['idx'], 'Status'] = "EXIT"; df.at[u['idx'], 'Exit_Price'] = u['ep']
                        conn.update(worksheet="INTRADAY_PORTFOLIO", data=df); st.rerun()
            rows = []; total_pnl = 0.0
            for _, r in active.iterrows():
                ltp, vwap, hod = get_vwap_data(r['Symbol'])
                entry, qty = float(r['Entry_Price']), int(float(r['Qty']))
                pnl = round((ltp - entry) * qty, 2); total_pnl += pnl
                base_sl = round(vwap - 2.0, 2)
                if hod >= (entry * 1.01): sys_sl = max(base_sl, entry, round(hod * 0.99, 2)); sl_t = "🔒 RATCHET"
                else: sys_sl = base_sl; sl_t = "🛡️ VWAP-2"
                rows.append({"Symbol": r['Symbol'], "Qty": qty, "Entry": entry, "LTP": ltp, f"SL ({sl_t})": sys_sl, "P&L": f"₹{pnl:,}", "Signal": "✅ HOLD" if ltp > sys_sl else "🚨 EXIT"})
            st.metric("Session P&L", f"₹{total_pnl:,.2f}"); st.dataframe(pd.DataFrame(rows), hide_index=True)
        except: pass
    live_intra()

with tab2:
    st.subheader("Step 1: Swing Engine")
    col_u, col_b = st.columns([2, 1])
    with col_u: choice = st.radio("Universe:", ["Nifty 500", "Microcap 250"], horizontal=True)
    with col_b: swing_alloc = st.number_input("Budget (₹)", 5000, 500000, 20000)
    if st.button(f"🚀 Scan {choice}"): st.session_state.swing_results = run_engine(5.0, use_sma_wall=True, universe=choice)
    if 'swing_results' in st.session_state and not st.session_state.swing_results.empty:
        df_s = st.session_state.swing_results.copy(); df_s['Qty'] = (swing_alloc / df_s['LTP']).astype(int)
        st.dataframe(df_s[['Rank', 'Symbol', 'LTP', 'Dist_Wall%', 'Qty']], hide_index=True)
        with st.form("swing_commit"):
            confirmed_s = []
            for _, r in df_s.iterrows():
                if r['Rank'] == "🔥 LEADER":
                    if st.checkbox(f"Allocate to {r['Symbol']}", key=f"sw_{r['Symbol']}"):
                        confirmed_s.append({'Symbol': r['Symbol'], 'Entry_Price': r['LTP'], 'Qty': r['Qty'], 'Date': get_now_ist().strftime('%Y-%m-%d'), 'Status': 'OPEN'})
            if st.form_submit_button("💾 COMMIT"):
                df_cur_s = conn.read(worksheet="SWING_PORTFOLIO", ttl=0).dropna(how='all')
                conn.update(worksheet="SWING_PORTFOLIO", data=pd.concat([df_cur_s, pd.DataFrame(confirmed_s)], ignore_index=True)); st.rerun()

with tab3:
    st.subheader("🛡️ Tactical ETF Aligner")
    etf_universe = fetch_etf_universe()
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
    fresh_cash = st.number_input("Fresh Cash (₹)", value=10000)
    if st.button("🔄 Scan ETF Universe"): st.session_state.run_etf_scan = True; st.session_state.pop("etf_top_6", None)
    if "json_holdings" not in st.session_state: st.session_state.json_holdings = pd.DataFrame([{"Symbol": "GOLDCASE", "Locked_Units": 3560, "Avg_Price": 26.42}])
    edited_holdings = st.data_editor(st.session_state.json_holdings, num_rows="dynamic")
    st.session_state.json_holdings = edited_holdings
    live_p = []; total_v = 0.0
    for _, r in edited_holdings.iterrows():
        sym, units, avg = str(r['Symbol']).upper(), int(r.get('Locked_Units', 0)), float(r.get('Avg_Price', 0))
        if sym and units > 0:
            ltp, _, _ = get_vwap_data(sym)
            if ltp == 0: ltp = avg
            val = units * ltp; total_v += val
            live_p.append({"Symbol": sym, "Units": units, "Value": round(val, 2)})
    if live_p: st.dataframe(pd.DataFrame(live_p), hide_index=True)
    if st.session_state.get('run_etf_scan', False):
        if "etf_top_6" not in st.session_state:
            results = []
            for s in etf_universe:
                try:
                    t = yf.Ticker(f"{s}.NS"); h = t.history(period="1y")
                    if len(h) < 60: continue
                    p = h['Close'].iloc[-1]
                    score = (p - h['Close'].iloc[-63]) / h['Close'].iloc[-63]
                    if score > 1.0 or score < -0.9: continue
                    vol = h['Close'].pct_change().tail(63).std() * np.sqrt(252)
                    results.append({'Category': categorize_etf(s), 'Symbol': s, 'LTP': p, 'Score': score, 'Inv_Vol': 1/vol})
                except: continue
            if results:
                df = pd.DataFrame(results).sort_values(by=['Category', 'Score'], ascending=[True, False]).drop_duplicates('Category')
                top_6 = df.sort_values('Score', ascending=False).head(6).copy()
                core = top_6.head(4); sum_v = core['Inv_Vol'].sum()
                top_6['Weight%'] = 0.0; top_6.iloc[0:4, top_6.columns.get_loc('Weight%')] = (top_6['Inv_Vol'] / sum_v) * 100
                st.session_state.etf_top_6 = top_6
        if "etf_top_6" in st.session_state: st.dataframe(st.session_state.etf_top_6[['Category', 'Symbol', 'LTP', 'Weight%']], hide_index=True)

# ==========================================
# TAB 4: SILENT SIGNAL (Regime Tracker)
# ==========================================
with tab4:
    st.subheader("🔭 SilentSignal - Trend Regime Tracker")
    st.markdown("Automatic Parity with Pine Script logic. Filters out 'Chop Zones' using ADX and 200 EMA.")
    
    # User-defined watchlist
    watchlist_str = st.text_input("Edit Watchlist (NS tickers, separate by comma)", value="^NSEI, ^NSEBANK, RELIANCE.NS, GOLDM26APR2026.MX, SILVERMIC.MX")
    watchlist = [x.strip() for x in watchlist_str.split(",")]
    
    tf = st.selectbox("Regime Timeframe", ["15m", "1h", "1d"], index=2)
    period_map = {"15m": "60d", "1h": "60d", "1d": "1y"}

    if st.button("🛰️ Scan Silent Signal"):
        ss_results = []
        prog_ss = st.progress(0, text="Calculating Pivot Trends...")
        with ThreadPoolExecutor(max_workers=5) as ex:
            futures = [ex.submit(calc_silent_signal, s, tf, period_map[tf]) for s in watchlist]
            for i, f in enumerate(futures):
                prog_ss.progress((i+1)/len(watchlist))
                res = f.result()
                if res: ss_results.append(res)
        prog_ss.empty()
        
        if ss_results:
            df_ss = pd.DataFrame(ss_results)
            st.dataframe(df_ss, hide_index=True, use_container_width=True)
            
            # Actionable Alerts
            for _, r in df_ss.iterrows():
                if "NEW" in r['Signal']:
                    st.toast(f"🚨 {r['Signal']} on {r['Symbol']}!", icon="🔥")
        else:
            st.error("No data fetched. Check ticker formatting (e.g. RELIANCE.NS or ^NSEI).")

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
            dist_wall = ((curr_p - sma200) / sma200) * 100
            
            # NEW: Fetch VWAP only for the leaders to keep the scan fast
            _, vwap, _ = get_vwap_data(sym)
            sys_sl = round(vwap * 0.999, 2)
            
            # NEW: Calculate how far the SL is from the current price
            sl_drop_pct = round(((curr_p - sys_sl) / curr_p) * 100, 2)
            
            # NEW: Warning flag if the SL drop is dangerous for 5X leverage (e.g., > 1.5% away)
            risk_warn = "⚠️ DEEP SL" if sl_drop_pct > 1.5 else "✅ TIGHT SL"
            
            return {
                'Symbol': sym, 'LTP': round(curr_p, 2), 
                'Max%': round(max_chg, 2), 'RVOL': round(rvol, 1),
                'Sys_SL': sys_sl, 'SL_Distance%': f"-{sl_drop_pct}%", 'Risk': risk_warn,
                'Dist_Wall%': round(dist_wall, 2)
            }
    except: pass
    return None

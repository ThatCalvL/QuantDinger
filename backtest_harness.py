"""
Standalone, faithful replication of the QuantDinger 30m Donchian Trend + ER
IndicatorStrategy for offline backtesting and refinement.

This is NOT the platform BacktestService. It mirrors the documented contract:
  - four-way edge-triggered signals (open/close long/short)
  - signal_mode = confirmed (signal on bar close), fill on NEXT bar open
  - exit owner = indicator (Donchian exit window + ATR chandelier)
  - engine stopLossPct = wide catastrophic intrabar backstop
  - entryPct capital fraction sizing, taker fees + slippage
Differences from the platform engine: fee/slippage model and exact
intrabar fill ordering are approximations. Use for relative comparison.
"""

import time
import numpy as np
import pandas as pd
import ccxt


# ---------------------------------------------------------------- data
def fetch_ohlcv(symbol: str, timeframe: str = "30m", total: int = 8000) -> pd.DataFrame:
    ex = ccxt.binance({"enableRateLimit": True})
    tf_ms = ex.parse_timeframe(timeframe) * 1000
    since = ex.milliseconds() - total * tf_ms
    rows = []
    while True:
        batch = ex.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
        if not batch:
            break
        rows += batch
        since = batch[-1][0] + tf_ms
        if len(batch) < 1000:
            break
        if since >= ex.milliseconds():
            break
        time.sleep(ex.rateLimit / 1000.0)
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])
    df = df.drop_duplicates("time").reset_index(drop=True)
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = df[c].astype("float64")
    return df


# ------------------------------------------------------- signal builder
def build_signals(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    df = df.copy()

    def edge(s):
        s = s.fillna(False).astype(bool)
        return s & ~s.shift(1).fillna(False)

    ema_trend = df["close"].ewm(span=p["ema_trend"], adjust=False).mean()

    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / p["atr_len"], adjust=False).mean()

    donchian_hi = df["high"].rolling(p["entry_len"]).max().shift(1)
    donchian_lo = df["low"].rolling(p["entry_len"]).min().shift(1)
    exit_lo = df["low"].rolling(p["exit_len"]).min().shift(1)
    exit_hi = df["high"].rolling(p["exit_len"]).max().shift(1)

    long_stop = df["high"].rolling(p["chand_len"]).max() - atr * p["atr_mult"]
    short_stop = df["low"].rolling(p["chand_len"]).min() + atr * p["atr_mult"]

    direction = (df["close"] - df["close"].shift(p["er_len"])).abs()
    volatility = df["close"].diff().abs().rolling(p["er_len"]).sum()
    er = direction / volatility.replace(0, np.nan)
    er_ok = er > p["er_min"]

    if p["use_volume"]:
        vol_ma = df["volume"].rolling(p["vol_len"]).mean()
        vol_ok = df["volume"] >= vol_ma * p["vol_mult"]
    else:
        vol_ok = pd.Series(True, index=df.index)

    trend_up = df["close"] > ema_trend
    trend_dn = df["close"] < ema_trend

    raw_open_long = (df["close"] > donchian_hi) & trend_up & er_ok & vol_ok
    raw_open_short = (df["close"] < donchian_lo) & trend_dn & er_ok & vol_ok
    if not p["allow_short"]:
        raw_open_short = raw_open_short & False

    raw_close_long = (df["close"] < exit_lo) | (df["close"] < long_stop.shift(1))
    raw_close_short = (df["close"] > exit_hi) | (df["close"] > short_stop.shift(1))

    df["open_long"] = edge(raw_open_long)
    df["open_short"] = edge(raw_open_short)
    df["close_long"] = edge(raw_close_long)
    df["close_short"] = edge(raw_close_short)
    df["atr"] = atr
    return df


# ----------------------------------------------------------- simulator
def build_signals_meanrev(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    """Keltner mean-reversion: fade band extremes only when ER says 'ranging'."""
    df = df.copy()

    def edge(s):
        s = s.fillna(False).astype(bool)
        return s & ~s.shift(1).fillna(False)

    mid = df["close"].ewm(span=p["bb_len"], adjust=False).mean()
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / p["atr_len"], adjust=False).mean()

    upper = mid + atr * p["band_mult"]
    lower = mid - atr * p["band_mult"]

    direction = (df["close"] - df["close"].shift(p["er_len"])).abs()
    volatility = df["close"].diff().abs().rolling(p["er_len"]).sum()
    er = direction / volatility.replace(0, np.nan)
    ranging = er < p["er_max"]

    rlen = p.get("rsi_len", 0)
    if rlen:
        d = df["close"].diff()
        g = d.clip(lower=0).ewm(alpha=1 / rlen, adjust=False).mean()
        ls = (-d.clip(upper=0)).ewm(alpha=1 / rlen, adjust=False).mean()
        rsi = 100 - (100 / (1 + g / ls.replace(0, np.nan)))
        rsi_long = rsi < p.get("rsi_buy", 30)
        rsi_short = rsi > (100 - p.get("rsi_buy", 30))
    else:
        rsi_long = pd.Series(True, index=df.index)
        rsi_short = pd.Series(True, index=df.index)

    raw_open_long = (df["close"] < lower) & ranging & rsi_long
    raw_open_short = (df["close"] > upper) & ranging & rsi_short
    if not p["allow_short"]:
        raw_open_short = raw_open_short & False

    raw_close_long = df["close"] >= mid
    raw_close_short = df["close"] <= mid

    df["open_long"] = edge(raw_open_long)
    df["open_short"] = edge(raw_open_short)
    df["close_long"] = edge(raw_close_long)
    df["close_short"] = edge(raw_close_short)
    df["atr"] = atr
    return df


def resample_htf(df: pd.DataFrame, rule: str = "4h") -> pd.DataFrame:
    d = df.copy()
    d["dt"] = pd.to_datetime(d["time"], unit="ms")
    d = d.set_index("dt")
    out = d.resample(rule).agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), volume=("volume", "sum"),
    ).dropna()
    return out.reset_index()


def build_signals_mtf(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    """30m Donchian breakout gated by a higher-timeframe (4H) EMA trend.
    Look-ahead safe: each 30m bar uses the most recent *completed prior* 4H bar."""
    sig = build_signals(df, p)
    htf = resample_htf(df, p.get("htf_rule", "4h"))
    htf["ema"] = htf["close"].ewm(span=p.get("htf_ema", 50), adjust=False).mean()
    htf["htf_up"] = (htf["close"] > htf["ema"]).shift(1)  # prior completed bar
    base = df.copy()
    base["dt"] = pd.to_datetime(base["time"], unit="ms")
    merged = pd.merge_asof(
        base[["dt"]], htf[["dt", "htf_up"]].dropna(), on="dt", direction="backward"
    )
    htf_up = merged["htf_up"].fillna(False).to_numpy()
    htf_up = pd.Series(htf_up, index=sig.index).astype(bool)
    sig["open_long"] = sig["open_long"] & htf_up
    sig["open_short"] = sig["open_short"] & (~htf_up)
    return sig


def backtest(df: pd.DataFrame, p: dict, cfg: dict) -> dict:
    fee = cfg["fee"]           # taker fee per side (fraction)
    slip = cfg["slippage"]     # fill slippage (fraction)
    entry_pct = cfg["entry_pct"]
    hard_stop = cfg["hard_stop_pct"]
    cash = cfg["initial_capital"]

    o = df["open"].values
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    ol = df["open_long"].values
    os_ = df["open_short"].values
    cl = df["close_long"].values
    cs = df["close_short"].values

    n = len(df)
    equity = cash
    side = None
    entry = 0.0
    units = 0.0
    stop_price = 0.0

    equity_curve = np.empty(n)
    equity_curve[:] = cash
    trades = []  # (pnl, ret)
    max_hold = cfg.get("max_hold", 0)
    bars_held = 0

    def close_at(price, idx):
        nonlocal equity, side, entry, units, stop_price
        if side == "long":
            pnl = units * (price - entry) - fee * units * (entry + price)
        else:
            pnl = units * (entry - price) - fee * units * (entry + price)
        equity += pnl
        ret = pnl / (units * entry) if units * entry else 0.0
        trades.append((pnl, ret))
        side = None
        entry = 0.0
        units = 0.0
        stop_price = 0.0

    for i in range(n - 1):
        # 1) intrabar catastrophic engine stop on bar i (price-based, immediate)
        if side == "long" and l[i] <= stop_price:
            close_at(stop_price * (1 - slip), i)
        elif side == "short" and h[i] >= stop_price:
            close_at(stop_price * (1 + slip), i)

        # 2) time stop (confirmed on i, fill next open i+1)
        if side is not None and max_hold and bars_held >= max_hold:
            close_at(o[i + 1] * (1 - slip) if side == "long" else o[i + 1] * (1 + slip), i + 1)

        # 3) signal-driven exits (confirmed on i, fill next open i+1)
        if side == "long" and cl[i]:
            close_at(o[i + 1] * (1 - slip), i + 1)
        elif side == "short" and cs[i]:
            close_at(o[i + 1] * (1 + slip), i + 1)

        # 4) entries (confirmed on i, fill next open i+1); R2-style: only when flat
        if side is None:
            if ol[i]:
                entry = o[i + 1] * (1 + slip)
                notional = equity * entry_pct
                units = notional / entry
                stop_price = entry * (1 - hard_stop)
                side = "long"
                bars_held = 0
            elif os_[i]:
                entry = o[i + 1] * (1 - slip)
                notional = equity * entry_pct
                units = notional / entry
                stop_price = entry * (1 + hard_stop)
                side = "short"
                bars_held = 0
        if side is not None:
            bars_held += 1

        # mark-to-market equity at close of i+1
        if side == "long":
            equity_curve[i + 1] = equity + units * (c[i + 1] - entry)
        elif side == "short":
            equity_curve[i + 1] = equity + units * (entry - c[i + 1])
        else:
            equity_curve[i + 1] = equity

    # ---- metrics
    eq = pd.Series(equity_curve)
    rets = eq.pct_change().fillna(0.0)
    total_return = eq.iloc[-1] / cash - 1.0
    roll_max = eq.cummax()
    max_dd = ((eq - roll_max) / roll_max).min()
    bars_per_year = 365 * 24 * 2  # 30m crypto
    sharpe = (rets.mean() / rets.std() * np.sqrt(bars_per_year)) if rets.std() > 0 else 0.0
    wins = [t for t in trades if t[0] > 0]
    losses = [t for t in trades if t[0] <= 0]
    gross_win = sum(t[0] for t in wins)
    gross_loss = -sum(t[0] for t in losses)
    pf = (gross_win / gross_loss) if gross_loss > 0 else float("inf")
    win_rate = (len(wins) / len(trades) * 100) if trades else 0.0
    return {
        "trades": len(trades),
        "total_return_pct": total_return * 100,
        "max_dd_pct": max_dd * 100,
        "sharpe": sharpe,
        "win_rate_pct": win_rate,
        "profit_factor": pf,
        "final_equity": eq.iloc[-1],
    }


def fmt(name, m):
    pf = m["profit_factor"]
    pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
    return (f"{name:<22} | trades {m['trades']:>4} | ret {m['total_return_pct']:>8.2f}% | "
            f"maxDD {m['max_dd_pct']:>7.2f}% | Sharpe {m['sharpe']:>6.2f} | "
            f"win {m['win_rate_pct']:>5.1f}% | PF {pf_s}")


BASE_PARAMS = dict(
    entry_len=30, exit_len=12, ema_trend=100, er_len=10, er_min=0.30,
    atr_len=14, chand_len=22, atr_mult=3.0,
    use_volume=True, vol_len=20, vol_mult=1.0, allow_short=True,
)
CFG = dict(
    initial_capital=10000.0, entry_pct=0.20, hard_stop_pct=0.08,
    fee=0.0004, slippage=0.0005,
)


import os
import itertools
import pickle


def load_data(symbols, total=8000):
    cache = "/tmp/qd_30m_cache.pkl"
    if os.path.exists(cache):
        with open(cache, "rb") as f:
            data = pickle.load(f)
        if all(s in data for s in symbols):
            return data
    data = {}
    for s in symbols:
        data[s] = fetch_ohlcv(s, "30m", total=total)
    with open(cache, "wb") as f:
        pickle.dump(data, f)
    return data


def mean_metrics(data, params, cfg, builder=build_signals):
    ms = []
    for s in data:
        ms.append(backtest(builder(data[s], params), params, cfg))
    n = len(ms)
    agg = {
        "trades": sum(m["trades"] for m in ms) / n,
        "ret": sum(m["total_return_pct"] for m in ms) / n,
        "dd": sum(m["max_dd_pct"] for m in ms) / n,
        "sharpe": sum(m["sharpe"] for m in ms) / n,
        "win": sum(m["win_rate_pct"] for m in ms) / n,
        "pf": sum(m["profit_factor"] for m in ms if m["profit_factor"] != float("inf")) / n,
    }
    return agg, ms


if __name__ == "__main__":
    symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
    data = load_data(symbols)
    for s in symbols:
        d = data[s]
        print(f"# {s}: {len(d)} bars  {pd.to_datetime(d['time'].iloc[0], unit='ms')} -> "
              f"{pd.to_datetime(d['time'].iloc[-1], unit='ms')}")

    print("=" * 110)
    print("BASELINE (as written)")
    print("=" * 110)
    for s in symbols:
        print(fmt(s, backtest(build_signals(data[s], BASE_PARAMS), BASE_PARAMS, CFG)))

    print("=" * 110)
    print("GRID SWEEP (ranked by mean return across symbols; require avg trades 15-160)")
    print("=" * 110)
    grid = dict(
        er_min=[0.35, 0.45, 0.55],
        entry_len=[30, 45],
        exit_len=[15, 25],
        atr_mult=[3.0, 4.5],
        ema_trend=[120, 200],
        allow_short=[True, False],
    )
    keys = list(grid.keys())
    results = []
    for combo in itertools.product(*[grid[k] for k in keys]):
        p = dict(BASE_PARAMS)
        p.update(dict(zip(keys, combo)))
        agg, _ = mean_metrics(data, p, CFG)
        if 15 <= agg["trades"] <= 160:
            results.append((agg, p))
    results.sort(key=lambda x: x[0]["ret"], reverse=True)
    for agg, p in results[:8]:
        tag = (f"er{p['er_min']} en{p['entry_len']} ex{p['exit_len']} "
               f"atr{p['atr_mult']} ema{p['ema_trend']} short{int(p['allow_short'])}")
        print(f"{tag:<46} | trades {agg['trades']:>5.0f} | ret {agg['ret']:>7.2f}% | "
              f"maxDD {agg['dd']:>6.2f}% | Sharpe {agg['sharpe']:>5.2f} | win {agg['win']:>4.1f}% | PF {agg['pf']:.2f}")

    print("=" * 110)
    print("MEAN-REVERSION SWEEP (fade Keltner bands when ranging; ranked by mean return)")
    print("=" * 110)
    mr_base = dict(bb_len=30, atr_len=14, band_mult=2.0, er_len=10, er_max=0.40, allow_short=True)
    mr_cfg = dict(CFG)
    mr_cfg["hard_stop_pct"] = 0.05
    mr_grid = dict(
        bb_len=[20, 30, 50],
        band_mult=[1.5, 2.0, 2.5, 3.0],
        er_max=[0.30, 0.40, 0.50],
        allow_short=[True, False],
    )
    mkeys = list(mr_grid.keys())
    mr_results = []
    for combo in itertools.product(*[mr_grid[k] for k in mkeys]):
        p = dict(mr_base)
        p.update(dict(zip(mkeys, combo)))
        agg, _ = mean_metrics(data, p, mr_cfg, builder=build_signals_meanrev)
        if 15 <= agg["trades"] <= 400:
            mr_results.append((agg, p))
    mr_results.sort(key=lambda x: x[0]["ret"], reverse=True)
    for agg, p in mr_results[:10]:
        tag = (f"bb{p['bb_len']} band{p['band_mult']} erMax{p['er_max']} short{int(p['allow_short'])}")
        print(f"{tag:<46} | trades {agg['trades']:>5.0f} | ret {agg['ret']:>7.2f}% | "
              f"maxDD {agg['dd']:>6.2f}% | Sharpe {agg['sharpe']:>5.2f} | win {agg['win']:>4.1f}% | PF {agg['pf']:.2f}")

    print("=" * 110)
    print("BUY & HOLD BENCHMARK (what the market itself did this window)")
    print("=" * 110)
    for s in symbols:
        c = data[s]["close"].values
        print(f"{s:<22} | buy&hold ret {(c[-1] / c[0] - 1) * 100:>8.2f}%")

    print("=" * 110)
    print("REFINED: long-only Keltner fade + RSI(2) confirm + time-stop (theory-driven)")
    print("=" * 110)
    refined = dict(bb_len=20, atr_len=14, band_mult=2.0, er_len=10, er_max=0.50,
                   allow_short=False, rsi_len=2, rsi_buy=10)
    ref_cfg = dict(CFG)
    ref_cfg["hard_stop_pct"] = 0.04
    ref_cfg["max_hold"] = 16  # ~8h on 30m; bail if reversion doesn't come
    agg, _ = mean_metrics(data, refined, ref_cfg, builder=build_signals_meanrev)
    print(f"MEAN across symbols      | trades {agg['trades']:>5.0f} | ret {agg['ret']:>7.2f}% | "
          f"maxDD {agg['dd']:>6.2f}% | Sharpe {agg['sharpe']:>5.2f} | win {agg['win']:>4.1f}% | PF {agg['pf']:.2f}")
    for s in symbols:
        print(fmt(s, backtest(build_signals_meanrev(data[s], refined), refined, ref_cfg)))

    print("=" * 110)
    print("MTF-ALIGNED breakout: 30m Donchian gated by 4H EMA trend (sweep over 4H EMA + er_min)")
    print("=" * 110)
    mtf_grid = dict(er_min=[0.35, 0.45], entry_len=[30, 45], atr_mult=[3.0, 4.5],
                    htf_ema=[20, 50], ema_trend=[100])
    mkeys2 = list(mtf_grid.keys())
    mtf_results = []
    for combo in itertools.product(*[mtf_grid[k] for k in mkeys2]):
        p = dict(BASE_PARAMS)
        p.update(dict(zip(mkeys2, combo)))
        agg, _ = mean_metrics(data, p, CFG, builder=build_signals_mtf)
        if agg["trades"] >= 10:
            mtf_results.append((agg, p))
    mtf_results.sort(key=lambda x: x[0]["ret"], reverse=True)
    for agg, p in mtf_results[:6]:
        tag = f"er{p['er_min']} en{p['entry_len']} atr{p['atr_mult']} htfEMA{p['htf_ema']}"
        print(f"{tag:<40} | trades {agg['trades']:>5.0f} | ret {agg['ret']:>7.2f}% | "
              f"maxDD {agg['dd']:>6.2f}% | Sharpe {agg['sharpe']:>5.2f} | win {agg['win']:>4.1f}% | PF {agg['pf']:.2f}")
    if mtf_results:
        print("-" * 110)
        print("Best MTF-aligned config, per symbol:")
        best = mtf_results[0][1]
        for s in symbols:
            print(fmt(s, backtest(build_signals_mtf(data[s], best), best, CFG)))

    print("=" * 110)
    print("DEPLOYABLE PROXY: 30m breakout w/ long EMA regime (~4H gate, sandbox-safe)")
    print("=" * 110)
    for et in [300, 400, 500]:
        p = dict(BASE_PARAMS, er_min=0.45, entry_len=45, exit_len=15, atr_mult=3.0, ema_trend=et)
        agg, _ = mean_metrics(data, p, CFG)
        print(f"ema_trend={et:<4} | trades {agg['trades']:>5.0f} | ret {agg['ret']:>7.2f}% | "
              f"maxDD {agg['dd']:>6.2f}% | Sharpe {agg['sharpe']:>5.2f} | win {agg['win']:>4.1f}% | PF {agg['pf']:.2f}")
    p = dict(BASE_PARAMS, er_min=0.45, entry_len=45, exit_len=15, atr_mult=3.0, ema_trend=400)
    print("  -> per symbol at ema_trend=400:")
    for s in symbols:
        print("   " + fmt(s, backtest(build_signals(data[s], p), p, CFG)))

    print("=" * 110)
    print("BREAKOUT NATIVE ON 4H (resampled; lower noise / fewer fees)")
    print("=" * 110)
    data4h = {s: resample_htf(data[s], "4h") for s in symbols}
    for agg_label, pset in [("4H er0.35 en20", dict(BASE_PARAMS, er_min=0.35, entry_len=20, exit_len=8, ema_trend=50)),
                            ("4H er0.45 en20", dict(BASE_PARAMS, er_min=0.45, entry_len=20, exit_len=8, ema_trend=50))]:
        line = []
        for s in symbols:
            m = backtest(build_signals(data4h[s], pset), pset, CFG)
            line.append(m)
        avg_ret = sum(m["total_return_pct"] for m in line) / 3
        print(f"{agg_label}")
        for s, m in zip(symbols, line):
            print("   " + fmt(s, m))

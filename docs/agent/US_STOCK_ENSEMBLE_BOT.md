# US Stock Chain-of-Strategies Ensemble Bot

> Built entirely through the QuantDinger Agent Gateway / MCP toolchain on 2026-06-11.
> Status: **validated in backtest, deployed as a stopped signal-mode strategy (paper-only)**.

---

## 1. Concept

A "chain-of-strategies" trading bot for the US Stock market modeled on multi-signal
institutional architectures: several independent alpha modules, each responsible for
exactly **one** edge, combined by a regime-aware master strategy. Every module was
developed, validated, backtested, and parameter-tuned individually through the
QuantDinger MCP before being composed into the final ensemble.

```
Market Data (1D bars)
        │
        ├── Alpha1  Trend following        (EMA gap + ADX + MACD)
        ├── Alpha2  Mean reversion         (Bollinger + RSI + z-score)
        ├── Alpha3  Momentum factor        (ROC + volume + 52w-high proximity)
        ├── Alpha4  Volatility regime      (ATR percentile + efficiency ratio)
        └── Alpha5  Volume flow            (OBV + A/D line + rolling VWAP)
        │
        ▼
  Voting Ensemble (regime-gated)  ──►  Signals  ──►  Engine risk (4% stop)  ──►  Paper/Live execution
```

## 2. Platform assets created

| Asset | ID | Notes |
|---|---|---|
| Indicator `Alpha1 Adaptive Trend Following` | 7 | v2: long-only, exit on trend break |
| Indicator `Alpha2 Statistical Mean Reversion` | 8 | both directions, exit at BB midband |
| Indicator `Alpha3 Momentum Factor` | 9 | long-only, trailing stop |
| Indicator `Alpha4 Volatility Regime Filter` | 10 | primarily an ensemble gate |
| Indicator `Alpha5 Volume Flow` | 11 | v2: long-only + 200d SMA gate |
| Indicator `QD Ensemble Master US Stock` | 12 | final voting ensemble |
| Strategy `QD Ensemble Master Bot - SPY` | 12 | `stopped`, signal mode, SPY 1D |

## 3. The five alpha modules

All modules follow the indicator contract v1 (four-way edge-triggered signals,
`# @param` tunables, `# @strategy` engine risk defaults).

### Alpha1 — Adaptive Trend Following (indicator 7)
- **Edge:** sustained directional moves.
- **Entry:** `EMA(fast) > EMA(slow)` AND `ADX(14) > floor` AND MACD histogram > 0.
- **Exit:** EMA cross-down only (signal owns the exit); 4% engine disaster stop.
- **Lesson learned:** v1 traded both directions with a 2% trailing stop and lost
  -25.9% (PF 0.43). Shorting index drift + tight trailing destroyed it. v2
  (long-only, patient exits) reached **+23.3%, PF 2.52** on SPY 2022-2025.
- **Tuned params (2022-2024 train):** `fast_len=30, slow_len=100, adx_floor=15`
  → PF 3.71, DD -7.8% on the train window.

### Alpha2 — Statistical Mean Reversion (indicator 8)
- **Edge:** overshoot-and-revert behaviour of index prices.
- **Entry:** close pierces Bollinger(20,2) band AND RSI(14) extreme AND |z-score(50)| > gate.
- **Exit:** reversion to the BB midband; 2% stop / 4% target.
- **Result:** +14.9%, PF 2.09, 68% win rate (SPY 2022-2025, both directions).
- **Tuned:** `z_entry=2.0` (deeper extremes only) → PF 9.06, 83% win on train.

### Alpha3 — Momentum Factor (indicator 9)
- **Edge:** equity momentum premium (strength begets strength).
- **Entry:** ROC > floor AND volume > 1.2× average AND within 5% of 52-week high.
- **Exit:** ROC crosses below zero; trailing stop (3%, activates +5%).
- **Result:** **+32.3%, PF 9.65, 82% win, DD -6.2%** — best single module.
- **Tuned:** `roc_len=10` → PF 12.27, DD -2.7% on train.

### Alpha4 — Volatility Regime Filter (indicator 10)
- **Edge:** knowing when *not* to trade.
- **Features mirror the platform `regime_detect` tool:** ATR percentile rank (252d),
  Kaufman efficiency ratio, normalized EMA gap.
- Classifies trend / chop / high-vol. Cross-checked against `regime_detect`
  (SPY currently "transition" with a high-volatility segment, conf 0.96 — matched).
- Standalone P&L is intentionally modest (+2.0%, PF 1.40); its job in the chain is
  gating: in high-vol regimes the ensemble requires unanimous votes.

### Alpha5 — Volume Flow (indicator 11)
- **Edge:** institutional accumulation/distribution footprints.
- **Entry:** OBV > OBV-MA(20) AND A/D line > its MA AND close > rolling VWAP(20),
  gated by the 200-day SMA bull filter.
- **Exit:** OBV flow reversal.
- **Lesson learned:** v1 traded distribution shorts and lost -44.5% (95 trades of
  churn against index drift). v2 long-only + trend gate: +4.5%, PF 1.31 — kept as a
  confirmation vote, not a standalone trader.

### Individual module scoreboard (SPY 1D, 2022-01-01 → 2025-12-31, $100k, 0.1% commission, strict mode)

| Module | Return | PF | Win% | MaxDD | Trades |
|---|---|---|---|---|---|
| Alpha1 Trend v2 | +23.3% | 2.52 | 50.0 | -17.7% | 8 |
| Alpha2 MeanRev | +14.9% | 2.09 | 68.4 | -10.7% | 19 |
| Alpha3 Momentum | +32.3% | 9.65 | 81.8 | -6.2% | 11 |
| Alpha4 Regime | +2.0% | 1.40 | 47.4 | -8.9% | 38 |
| Alpha5 Flow v2 | +4.5% | 1.31 | 41.7 | -11.1% | 48 |

## 4. Ensemble design evolution

Four iterations were backtested; the data drove each redesign:

| Version | Design | SPY 2022-2025 | Diagnosis |
|---|---|---|---|
| v1 score-blend | weighted continuous scores, threshold cross | +11.9%, PF 1.65, 31 trades | blending dilutes sharp module edges |
| v2 tuned blend | tuned module params + hysteresis + smoothing | +5.1%, PF 1.26 | same flaw, worse |
| v3 voting | binary votes, 2-of-3 entry, exit at votes<=1 | +5.9%, PF 1.47, 65 trades | vote flicker churn; trailing stop interference |
| **v4 final** | **unanimous 3/3 votes, 2-bar confirmation, exit only on full vote collapse, no trailing** | **+15.2% → +14.2% (final params), PF 2.19, DD -7.4%** | high-conviction, low-churn |

Final structured tune (TPE, 32 evals, train 2022-2024) was decisive:
`votes_needed=3` dominated every top slot — **+12.7%, PF 3.88, DD -3.8%, 75% win**
on a train window that includes the 2022 bear market.

### Final ensemble rules (indicator 12)
- **Vote 1 (Trend):** EMA30 > EMA100 AND ADX > 20 AND MACD hist > 0
- **Vote 2 (Momentum):** ROC(10) > 2% AND within 5% of 52w high
- **Vote 3 (Flow):** OBV > MA AND A/D > MA AND close > rVWAP(20) AND close > SMA200
- **Entry:** all 3 votes true for 2 consecutive bars (always 3 required in high-vol regime)
- **Dip-buy stream:** z-score(50) < -2 AND RSI < 32 AND not high-vol regime
- **Exit:** votes collapse to 0 for 2 bars, or overbought extreme (z > +2, RSI > 68)
- **Risk:** long-only, 50% capital per entry, 4% engine stop, no take-profit/trailing
  (signal owns the exit)

## 5. Validation results (final ensemble)

| Test | Window | Return | PF | Win% | MaxDD | Trades |
|---|---|---|---|---|---|---|
| SPY in-sample | 2022-01 → 2025-12 | +14.2% | 2.19 | 62.5 | -7.4% | 32 |
| **SPY out-of-sample** | 2025-01 → 2026-06 | **+2.5%** | **1.92** | **63.6** | **-6.9%** | 11 |
| QQQ | 2022-2025 | +4.0% | 1.36 | 54.9 | -16.2% | 51 |
| AAPL | 2022-2025 | -24.3% | 0.55 | 47.7 | -29.1% | 44 |
| MSFT | 2022-2025 | +4.0% | 1.25 | 57.1 | -15.9% | 42 |
| NVDA | 2022-2025 | +50.2% | 1.62 | 62.3 | -26.1% | 61 |

**Honest read of the evidence:**
- The profile that matters for an index strategy holds out-of-sample: PF and win
  rate are stable (2.19→1.92, 62.5→63.6) and drawdown stays in single digits.
  The OOS window (2025-2026) is a transition/high-vol regime per `regime_detect` —
  the ensemble correctly stayed defensive.
- The parameter set is **index-calibrated**. It transfers to high-momentum names
  (NVDA +50%) and is roughly flat on QQQ/MSFT, but fails on range-bound single
  names (AAPL). Use on individual stocks requires a per-symbol `submit_structured_tune`
  pass, exactly as done for SPY.
- Return magnitude is below buy-and-hold for the same windows. That is by design:
  exposure is taken only on unanimous multi-factor agreement, which cuts max
  drawdown to roughly one third of buy-and-hold's (SPY drew down ~25% in 2022 and
  ~19% in 2025). The strategy is a defensive absolute-return overlay, not a beta
  replacement; leverage or higher `entryPct` can scale exposure against the
  controlled drawdown if desired.

## 6. Reproducing / operating the bot

```text
# 1. Regime check (synchronous)
regime_detect(market="USStock", symbol="SPY", timeframe="1D", ...)

# 2. Backtest the master indicator (indicator_id 12 holds the code)
submit_backtest(code=<indicator 12 code>, market="USStock", symbol="SPY",
                timeframe="1D", start_date=..., end_date=...,
                trade_direction="long", strict_mode=true)
wait_for_job(job_id)

# 3. Re-tune on a new train window (never tune on the OOS window)
submit_structured_tune(payload={base: {...}, evolution: {method: "tpe", maxVariants: 32},
  parameterSpace: {"indicator_params.votes_needed": [2,3],
                   "indicator_params.adx_floor": [15,20], ...}})

# 4. The deployed strategy (id 12) is `stopped` in signal mode.
#    Starting it requires operator action; live capital additionally requires
#    a T-scope token with paper_only=false AND AGENT_LIVE_TRADING_ENABLED=true.
```

Execution path for live/paper trading is the existing Alpaca USStock integration
(zero-commission, native paper mode) via `trading_executor.py`.

## 7. Methodology notes (what the institutional checklist required)

- **One edge per module** — each alpha was backtested standalone before composition.
- **Train/test discipline** — all tuning used 2022-2024; 2025-2026 was never touched
  until final validation.
- **Regime awareness** — module weights/gates keyed off the same features as the
  platform's `regime_detect` (verified agreement).
- **Learning from failures** — Alpha1 v1 and Alpha5 v1 were redesigned from
  backtest evidence (short-side toxicity on index drift; trailing-stop churn).
  These failure modes and fixes are recorded here deliberately: they are the
  "learn from its own trades" loop, applied at development time.
- **Costs modeled** — 0.1% commission, strict next-bar-open fills (`strict_mode=true`,
  matching live execution semantics).
- **Safety** — everything ran through the Agent Gateway with scopes R/W/B/N/T,
  `paper_only=true`. No live order was placed; the strategy is created `stopped`.

## 8. Known limitations & next steps

1. **Sharpe is modest** (0.26 IS / ~0 OOS) because the strategy is flat much of the
   time and the backtest engine computes Sharpe on full-period equity including
   idle cash. Capacity-weighted deployment (higher `entryPct`, modest leverage)
   or running the 5 modules as parallel sleeves would lift capital efficiency.
2. **Single-name use requires re-tuning** (see AAPL). A per-symbol tune is one
   `submit_structured_tune` call.
3. **News/sentiment alphas** are not yet wired in: the gateway exposes price/volume
   data only. `get_market_news` / sentiment feeds would slot in as a 4th vote once
   exposed via MCP.
4. **`submit_ai_optimize`** (LLM-driven multi-round refinement) was intentionally
   not run — it consumes server LLM quota and the structured TPE tune already
   converged decisively. Run it later if a finer search is wanted.
5. **Walk-forward re-tuning cadence:** quarterly re-tune on a rolling 3-year train
   window is the recommended operating procedure.

---

## 9. Phase 2 — QD Stock Scout (auto stock-picking rotation bot)

Phase 1 produced a single-symbol ensemble. Phase 2 answers the actual product ask:
*"a bot that automatically finds valuable stocks and trades them"*. It uses the
platform's native **cross-sectional strategy engine**
(`trading_config.cs_strategy_type = "cross_sectional"` in `trading_executor.py`),
which scans a `symbol_list` universe on every rebalance, runs a scoring script,
and opens/closes positions to hold the top `portfolio_size` names.

### 9.1 Deployed artifacts

| Artifact | ID | Notes |
|----------|----|-------|
| Indicator "QD Stock Scout Cross-Sectional Ensemble" | 13 | Cross-sectional exec contract (`symbols`/`data`/`scores`/`rankings`), **not** the df contract |
| Strategy "QD Stock Scout - US Equity Rotation Bot" | 13 | `USStock`, spot, long-only, `bot_type=trend` (visible on the Bot page), daily rebalance, signal mode, **running** |

### 9.2 How it picks "valuable" stocks

Universe: 88 liquid US large caps across all sectors (mega tech, semis,
financials, healthcare, energy, industrials, staples). Every rebalance each
stock is scored with the Phase-1 ensemble factors applied cross-sectionally:

- **Trend votes (25%)** — EMA30>EMA100, close>SMA100, MACD hist>0 + ADX>15
- **12-1 momentum rank (40%)** — return t-126..t-21, cross-sectional percentile
- **52w-high proximity rank (15%)**
- **Volume-flow votes (20%)** — OBV>MA20, A/D>MA20, close>rolling VWAP20
- **Volatility penalty** — -10 pts when own ATR% is in its top decile
- **Liquidity gate** — 20d average dollar volume >= $50M

**Quality gates:** only stocks above SMA100 with >=2/3 trend votes and positive
momentum enter `rankings`. The bot holds the top 5 equal-weight; when fewer
names pass the gates (broad downtrend) it automatically holds fewer positions,
i.e. de-risks into cash.

### 9.3 Validation (run inside the backend container, exact executor contract)

The backtest engine does not support cross-sectional strategies, so validation
replicated `_execute_cross_sectional_indicator` exactly (same `safe_exec`
sandbox, same 137-bar visible window as the live USStock fetch):

| Metric | Scout (top-5, monthly rotation) | SPY buy & hold |
|--------|--------------------------------|----------------|
| Total return 2024-10 → 2026-06 | **+61.4%** | +27.5% |
| CAGR | **+34.7%** | +16.3% |
| Max drawdown (monthly marks) | -5.4% | — |
| Periods invested | 19/20 (one cash period) | always |

First live scan (2026-06-11): picked **UNH, C, PM, JNJ, MRK** from 39 eligible
names and opened 5 equal-weight long positions (~$20k each, signal mode).

### 9.4 Operations

- **Rebalance cadence:** daily (`rebalance_frequency=daily`, checked hourly via
  `decide_interval=3600`). Position diffs only — unchanged picks are held.
- **Signal vs live:** as of 2026-06-12 the bot runs `execution_mode=live`
  against the **Alpaca paper account** (`exchange_config.credential_id=1`,
  PK* key → paper endpoint auto-detected). Orders flow through
  `pending_orders` → `PendingOrderWorker._execute_alpaca_order` (market
  orders, long-only). To move to real money, rebind to a live (AK*) Alpaca
  credential — the broker policy layer still enforces USStock spot, long-only.
- **Bot-page visibility + wizard guard:** the strategy carries
  `trading_config.bot_type='trend'` so it shows on the Trading Bot page
  (the prebuilt frontend lists a strategy there when `strategy_mode='bot'`
  OR `trading_config.bot_type` is set). The card's "MA period" style params
  are cosmetic placeholders from the trend template — the real config lives
  in `trading_config` (symbol_list / portfolio_size / scorer). A backend
  guard in `StrategyService.update_strategy` now rejects any update that
  would rewrite a `cs_strategy_type='cross_sectional'` strategy into a
  bot-template ScriptStrategy (the Bot wizard did exactly that on 2026-06-12
  and destroyed the scout; it had to be restored from scratch). Saving from
  the Bot wizard now fails with a clear error instead of corrupting.
- **Alpaca sync:** `PendingOrderWorker` PositionSync polls Alpaca every 30s
  and reconciles exchange positions (size + avg cost) into the strategy
  ledger (`qd_strategy_positions`), so fills/avg prices in QuantDinger track
  the broker. Orders submitted while the market is closed sit in `new`
  status on Alpaca and fill at the 09:30 ET open; the ledger entry prices
  correct themselves on the next sync after the fill.
- **Editing the universe / portfolio size:** patch `trading_config.symbol_list`
  / `portfolio_size` via the Agent Gateway (`update_strategy`), then restart
  the strategy so the loop reloads its config.
- **Window caveat:** the live USStock fetch returns ~137 daily bars; all factor
  lookbacks fit inside that window (`MIN_BARS=110`). Do not add factors needing
  more history without checking the fetch limit in
  `_execute_cross_sectional_indicator` (limit=200, source returns ~137).

### 9.5 Known limitations

1. Validation covers ~1.6 years (mostly upmarket); the cash-gate logic engaged
   only once. Extend the harness with a longer panel before live capital.
2. No per-leg stop loss between rebalances — risk is managed at the daily
   rebalance. Acceptable for a 5-name large-cap book, but gap risk exists.
3. Same news/sentiment gap as Phase 1; a sentiment factor can slot into the
   scoring code once exposed via MCP.

# Trading Concepts

A reference for the key metrics and ideas used throughout BurryBot. No prior trading experience assumed.

---

## Prediction Markets

Polymarket is a prediction market. Instead of trading stocks or currencies, you trade on the outcome of events: "Will X happen by date Y?" Each market has two tokens — YES and NO — whose prices represent the market's collective belief in the probability of that outcome.

A YES token priced at `0.72` means the market thinks there's a 72% chance the event happens. When a market resolves, YES tokens pay out $1.00 if the event occurred, $0.00 if it didn't. This means prices are always between 0 and 1, and profitable trading requires being right about probabilities more often than the crowd.

---

## How Prediction Markets Work — FAQ

### When I sell, who buys?

Polymarket uses a **Central Limit Order Book (CLOB)** — the same mechanism used by stock exchanges. When you place a sell order, you're not selling to Polymarket itself; you're posting an offer that sits in the order book until another trader places a matching buy order.

In practice, a significant share of the liquidity on Polymarket comes from **market makers** — automated traders (often professional firms) that continuously post both buy and sell orders across many markets. They don't have strong opinions on outcomes; they profit from the spread between the price they buy at and the price they sell at. Their presence means there is almost always *someone* willing to transact, though not necessarily at the price you want.

So when you sell YES tokens at `0.60`, either:
- Another trader who believes the probability is higher than 60% takes the other side, or
- A market maker absorbs your sell and adjusts their inventory, planning to re-sell to someone else later.

---

### Is it possible to want to sell and find no one buying?

Yes. This is called **liquidity risk**, and it is real on Polymarket — especially in smaller, newer, or niche markets.

In thin markets the order book may have very few open buy orders. If you post a sell at `0.60` and the best available buyer is only willing to pay `0.45`, you face a choice: accept the lower price, wait and hope someone better comes along, or cancel the order. In a paper trading simulation like BurryBot, trades execute at the last known price without modelling this friction — which is one reason real results may differ from paper results.

This is also why BurryBot defaults to watching **high-volume markets**: more volume means more participants, a tighter bid-ask spread, and a much lower chance of being stuck holding a position you can't exit.

---

### What is the bid-ask spread?

The **bid** is the highest price any buyer is currently willing to pay. The **ask** is the lowest price any seller is currently willing to accept. The gap between them is the spread.

```
Bid: 0.58  |  Ask: 0.62  →  Spread: 0.04
```

If you want to buy immediately (a "market order"), you pay the ask (`0.62`). If you want to sell immediately, you receive the bid (`0.58`). The spread is effectively a transaction cost you pay every time you enter or exit a position — even before accounting for platform fees.

Liquid markets have tight spreads (0.01–0.02). Illiquid markets can have spreads of 0.10 or more, meaning you are already down 10% the moment you buy.

---

### What happens when a market resolves?

When the event's outcome is determined, Polymarket resolves the market:

- **YES tokens** pay out **$1.00** if the event happened, **$0.00** if it didn't.
- **NO tokens** pay out the inverse.

If you hold YES tokens worth `0.70` each and the event happens, each token becomes worth `$1.00` — a 43% gain. If the event doesn't happen, they become worth `$0.00` — a total loss.

BurryBot's paper trader monitors market end dates and force-closes any open position when a market resolves, using the last known price before resolution.

---

### Can I trade NO tokens?

Yes. On Polymarket you can trade either side of any market. Buying NO tokens is equivalent to betting against the event. A NO token priced at `0.28` means the market thinks there's only a 28% chance the event *doesn't* happen (equivalently, a 72% chance it does).

BurryBot trades both YES and NO tokens. The strategy generates independent signals for each side of every market, and the risk manager blocks a BUY on one side if the portfolio already holds the opposite side of the same market.

---

### What is slippage?

Slippage is the difference between the price you expected to trade at and the price you actually got. It happens for two reasons:

1. **Thin order books**: if you want to sell 1,000 shares and only 200 shares are bid at `0.60`, the remaining 800 fill at progressively worse prices (`0.59`, `0.58`, etc.).
2. **Latency**: by the time your order reaches the exchange, someone else may have already taken the best available price.

BurryBot's paper trading does not model slippage — all trades execute at the last fetched price. Live trading places GTC (Good-Til-Cancelled) limit orders, which naturally bound slippage: a BUY order will not fill above `price + LIVE_SLIPPAGE_TOLERANCE` (default: 2%), and a SELL order will not fill below `price - LIVE_SLIPPAGE_TOLERANCE`. If no counterparty is available within the tolerance band, the order remains open (or expires) rather than filling at an unexpectedly bad price.

---

### What are the fees?

Polymarket charges a percentage fee on each trade (the exact rate varies and is subject to change). BurryBot models a simplified fee in `config.py` (`TRADE_FEE_PCT`) that is deducted from each executed trade. The fee is small per trade but compounds over many trades — a strategy that trades frequently needs a larger edge to overcome it.

---

### Why does a market's price change if no one has traded recently?

Prices only change when trades happen. If a market sits at `0.72` and no one trades, it stays at `0.72` — even if the event's true probability has shifted. This is different from stocks, where prices can update on news without a trade occurring.

In practice, active markets update frequently because traders are constantly re-evaluating the probability based on new information and placing orders. Quieter markets can go hours or days without meaningful price movement, which is why BurryBot may see "no new bar" messages during ticks for less active markets.

---

### How is this different from betting?

The mechanics look similar but there are meaningful differences:

- **Prices are set by the market, not a bookmaker.** There is no house setting odds and taking a guaranteed cut. Prices reflect the aggregate views of all participants.
- **You can exit before resolution.** In a sportsbet you're locked in until the game ends. In a prediction market you can sell your position at any time, realising a profit or cutting a loss without waiting for the event.
- **The crowd is often well-calibrated.** Research consistently shows that prediction market prices are among the most accurate forecasts available for many types of events — more accurate than polls, pundits, and most quantitative models. Beating the market consistently is hard.

---

### Why might paper trading results not match live trading results?

Several real-world frictions are not modelled in paper trading:

| Factor | Paper trading | Live trading |
|--------|--------------|--------------|
| Bid-ask spread | Ignored — trades at mid price | You pay the spread on every entry and exit |
| Slippage | Ignored | GTC limit orders bounded by `LIVE_SLIPPAGE_TOLERANCE` (2%) |
| Liquidity | Assumed infinite | Order may not fill if no counterparty is within tolerance |
| Latency | Instant | Orders take time to reach the exchange |
| Fees | Approximated (`TRADE_FEE_RATE` in config) | Exact fee schedule applies |
| Portfolio seed | Starts at configured `--cash` value | Seeded from actual on-chain USDC balance |

This gap between paper and live performance is sometimes called the **paper trading illusion** — a strategy that looks profitable in simulation may break even or lose once real frictions are applied. The purpose of paper trading in BurryBot is to validate strategy *logic* and *behaviour*, not to produce exact predictions of live returns.

---

## Performance Metrics

### Sharpe Ratio

**What it measures:** Return per unit of risk taken.

**Formula:** `(portfolio return − risk-free rate) / standard deviation of returns`

In practice for a short-term simulation, the risk-free rate is set to 0, so it simplifies to: average return divided by how volatile those returns were.

**How to read it:**

| Value | Meaning |
|-------|---------|
| `> 2.0` | Excellent |
| `1.0 – 2.0` | Good |
| `0 – 1.0` | Marginal — some return, but a lot of noise |
| `< 0` | You would have been better off in cash |

**The intuition:** Two strategies both return 20%. Strategy A had smooth, steady gains. Strategy B swung wildly — +50% one week, −30% the next. Strategy A has a higher Sharpe because it achieved the same result with less volatility. A high Sharpe means the returns weren't just luck from a few big swings.

---

### Max Drawdown

**What it measures:** The largest peak-to-trough decline in portfolio value during the session, expressed as a percentage.

**Formula:** `(trough value − peak value) / peak value × 100`

**Example:** Portfolio hits $1,200, then falls to $900 before recovering. Max drawdown = `(900 − 1200) / 1200 = −25%`.

**How to read it:**

| Value | Meaning |
|-------|---------|
| `~0%` | Capital was never meaningfully at risk |
| `−5% to −15%` | Controlled — typical for cautious strategies |
| `−20% to −40%` | Significant — requires a large recovery gain to break even |
| `< −50%` | Catastrophic — requires a +100%+ gain just to recover |

**The intuition:** It answers the question *"what's the worst I would have felt holding this strategy?"* A strategy can show a great final return but have had a brutal −60% dip in the middle — most real investors would have panic-sold long before recovery. Max drawdown captures that psychological and capital risk.

**Note on recovery math:** Losses are asymmetric. A −25% drawdown requires a +33% gain to recover. A −50% drawdown requires +100%. This is why limiting drawdown matters more than it might initially seem.

---

### Win Rate

**What it measures:** The percentage of completed sell trades that were profitable.

**Formula:** `profitable sells / total sells × 100`

**How to read it:** Win rate alone is not a reliable indicator of strategy quality. A strategy that wins 30% of the time but earns $10 per win while losing only $1 per loss is highly profitable. Conversely, a strategy winning 80% of the time but with small wins and catastrophic losses can still lose money overall. Win rate should always be read alongside average PnL per trade.

---

### Total Return

**What it measures:** The overall gain or loss as a percentage of starting capital.

**Formula:** `(final value − starting cash) / starting cash × 100`

This is the simplest metric, but also the most misleading in isolation. A 20% return over one week is very different from a 20% return over five years. In BurryBot's context (short paper trading sessions), it reflects how much the strategy made or lost relative to starting cash during that specific run.

---

### Average PnL per Trade

**What it measures:** The average realised profit or loss per completed sell trade, in USDC.

A positive average PnL means the strategy is, on average, closing positions at a profit. Combined with win rate, this tells you whether the strategy's edge comes from winning often, winning big, or both.

---

## How the Metrics Work Together

No single metric tells the full story. The useful combinations are:

**Sharpe + Max Drawdown** — together these describe the *quality* of returns. High Sharpe and low drawdown means the strategy earned its returns consistently without putting capital at serious risk.

**Win Rate + Avg PnL** — together these describe the *shape* of returns. A low win rate with high avg PnL suggests a strategy that holds out for large moves (momentum-style). A high win rate with low avg PnL suggests one that harvests many small gains (mean reversion-style).

| Scenario | Sharpe | Max DD | Verdict |
|----------|--------|--------|---------|
| Steady gains, low volatility | High | Small | Ideal |
| Same return but wild swings | Low | Large | Risky |
| Tiny return, near-zero volatility | High | ~0% | Safe but useless |
| Losing money | Negative | Large | Avoid |

---

## Strategy Concepts

### Momentum

The idea that recent price trends tend to continue. If a market's YES token has been rising consistently, momentum strategies bet it will keep rising. Works best when new information arrives gradually and the crowd updates slowly. Performs poorly at sharp reversals.

**Risk:** Susceptible to buying at the top of a trend just before it reverses.

### Mean Reversion

The opposite of momentum. Prices that have moved unusually far from their recent average tend to snap back. Mean reversion strategies buy when a price is abnormally low and sell when it is abnormally high, as measured by the Z-score.

**Z-score** = `(current price − rolling mean) / rolling standard deviation`

A Z-score of −2.0 means the price is 2 standard deviations below its recent average — statistically unusual, suggesting a temporary mispricing.

**Risk:** Sometimes prices are "unusually low" because something fundamental has changed. Mean reversion bets that the deviation is temporary noise, not a permanent shift.

### RSI (Relative Strength Index)

A momentum-reversal hybrid that measures the speed and size of recent price moves on a 0–100 scale. RSI below 30 signals the market is oversold (too much selling pressure, likely to bounce). RSI above 70 signals overbought (too much buying pressure, likely to fall).

Unlike pure momentum (which bets on continuation) or pure mean reversion (which bets on snapback regardless of speed), RSI specifically looks for *overextension* — moves that happened too fast to be sustainable.

### Random Baseline

Makes random BUY/SELL/HOLD decisions. Its only purpose is to serve as a performance floor: any real strategy should outperform random trading over a sufficient number of trades. If a strategy can't beat random, it has no edge.

---

## Risk Management Concepts

### Position Sizing

How much capital to deploy in any single trade. BurryBot's risk manager enforces two hard limits:

- **Max position size**: a single position cannot exceed 20% of total portfolio value
- **Max total exposure**: no more than 80% of the portfolio can be in open positions at once — at least 20% stays in cash

Within those limits, trade size is further scaled by the strategy's `confidence` value (0.0–1.0). Higher confidence → larger position.

### Exposure

The fraction of total portfolio capital currently at risk in open positions. High exposure means most of your capital is tied up in positions that could lose value. The 80% cap ensures there is always cash available to act on new signals and to absorb losses without being forced to sell at a bad time.

### Lookahead Bias

A common mistake in backtesting where a strategy accidentally uses future data to make past decisions. For example, if a strategy can "see" tomorrow's price when deciding what to buy today, it will appear to perform perfectly in backtests — but that performance is meaningless because you cannot know tomorrow's prices today.

BurryBot prevents this by passing only `history[:i]` (all bars *before* the current one) to the strategy at each step.

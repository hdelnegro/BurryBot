# Trading Concepts

A reference for the key metrics and ideas used throughout BurryBot. No prior trading experience assumed.

---

## Prediction Markets

Polymarket is a prediction market. Instead of trading stocks or currencies, you trade on the outcome of events: "Will X happen by date Y?" Each market has two tokens — YES and NO — whose prices represent the market's collective belief in the probability of that outcome.

A YES token priced at `0.72` means the market thinks there's a 72% chance the event happens. When a market resolves, YES tokens pay out $1.00 if the event occurred, $0.00 if it didn't. This means prices are always between 0 and 1, and profitable trading requires being right about probabilities more often than the crowd.

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

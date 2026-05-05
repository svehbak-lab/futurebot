# FutureBot 🤖

Paper trading bot for Oslo Børs based on sentiment divergence signals from [SentimentMood](https://sentimentmood.com).

## Strategy

- **Signal:** vs_sector divergence > 0.25 (company sentiment significantly above sector average)
- **Hold period:** 3 trading days
- **Stop loss:** 3% per position
- **Position sizing:** 100% of portfolio equally split across all signals (100% / N)
- **Start capital:** NOK 100,000 (paper money)

## Backtested results (Jan–Apr 2026)

| Setup | Return | vs B&H |
|---|---|---|
| No stop loss | +57.27% | +30.20pp |
| 3% stop loss | +74.03% | +46.96pp |

## Files

```
src/
  signal_reader.py     — reads tonight's signals from Turso
  place_orders.py      — places paper trades at 09:00 Oslo
  exit_positions.py    — closes positions at 16:20 Oslo
  dashboard.html       — portfolio dashboard

data/
  signals_tonight.json — tonight's signals (auto-updated)
  positions.json       — open positions (auto-updated)
  portfolio.json       — cash + start capital
  portfolio_history.csv — daily portfolio value
  trade_log.csv        — all completed trades

.github/workflows/
  bot.yml              — 3 scheduled jobs
```

## GitHub Secrets required

Add these in Settings → Secrets → Actions:

- `TURSO_URL` — your Turso database URL
- `TURSO_TOKEN` — your Turso auth token

## Schedule

| Job | Time (Oslo) | What it does |
|---|---|---|
| signal_reader | 23:10 Mon-Fri | Reads tonight's signals from Turso |
| place_orders | 09:00 Mon-Fri | Places paper trades |
| exit_positions | 16:20 Mon-Fri | Closes positions after 3 days or stop loss |

## Dashboard

Open `src/dashboard.html` in a browser (or host it as a GitHub Pages site) to see:
- Portfolio value over time
- Open positions
- Trade history with P&L
- Win rate

## Important

This is **paper trading only** — no real money involved. Do not use this for real trades without extensive validation. Past backtested performance does not guarantee future results.

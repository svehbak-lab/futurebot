"""
place_orders.py
Reads last night's signals and places paper trades.
Saves open positions to data/positions.json
Runs at 09:00 Oslo Mon-Fri.
"""

import os, json, urllib.request
from datetime import date, datetime, timezone

TURSO_URL   = os.environ["TURSO_URL"]
TURSO_TOKEN = os.environ["TURSO_TOKEN"]
DATA_DIR    = os.path.join(os.path.dirname(__file__), "..", "data")
STOP_LOSS   = 0.03   # 3% stop loss
HOLD_DAYS   = 3      # trading days to hold


def turso(sql):
    url = TURSO_URL.replace("libsql://", "https://") + "/v2/pipeline"
    req = urllib.request.Request(
        url,
        data=json.dumps({"requests": [{"type": "execute", "stmt": {"sql": sql}}, {"type": "close"}]}).encode(),
        headers={"Authorization": f"Bearer {TURSO_TOKEN}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    result = data["results"][0]["response"]["result"]
    cols   = [c["name"] for c in result["cols"]]
    return [dict(zip(cols, [v.get("value") for v in row])) for row in result["rows"]]


def get_trading_days_ahead(from_date, n):
    """Get the date N trading days from from_date using stock_prices."""
    rows = turso(f"""
        SELECT DISTINCT date FROM stock_prices
        WHERE date > '{from_date}'
        ORDER BY date ASC
        LIMIT {n}
    """)
    if len(rows) >= n:
        return rows[n-1]["date"]
    return None


def get_current_price(ticker):
    today = date.today().isoformat()
    rows = turso(f"""
        SELECT close FROM stock_prices
        WHERE ticker = '{ticker}' AND date <= '{today}'
        ORDER BY date DESC LIMIT 1
    """)
    return float(rows[0]["close"]) if rows and rows[0]["close"] else None


def load_portfolio():
    path = os.path.join(DATA_DIR, "portfolio.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {
        "cash":          100_000.0,
        "start_capital": 100_000.0,
        "created":       date.today().isoformat(),
    }


def save_portfolio(portfolio):
    path = os.path.join(DATA_DIR, "portfolio.json")
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(path, "w") as f:
        json.dump(portfolio, f, indent=2)


def load_positions():
    path = os.path.join(DATA_DIR, "positions.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return []


def save_positions(positions):
    path = os.path.join(DATA_DIR, "positions.json")
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(path, "w") as f:
        json.dump(positions, f, indent=2)


def main():
    today = date.today().isoformat()
    print(f"[ORDERS] Placing orders for {today}...", flush=True)

    # Load signals from last night
    sig_path = os.path.join(DATA_DIR, "signals_tonight.json")
    if not os.path.exists(sig_path):
        print("[ORDERS] No signals file found — skipping", flush=True)
        return

    with open(sig_path) as f:
        sig_data = json.load(f)

    signal_date = sig_data.get("date")
    signals     = sig_data.get("signals", [])

    if not signals:
        print("[ORDERS] No signals tonight — no trades placed", flush=True)
        return

    # Check if we already traded today
    positions = load_positions()
    already_traded = any(p["entry_date"] == today for p in positions if p["status"] == "open")
    if already_traded:
        print("[ORDERS] Already traded today — skipping", flush=True)
        return

    # Load portfolio
    portfolio = load_portfolio()
    cash      = portfolio["cash"]

    if cash <= 0:
        print(f"[ORDERS] No cash available (NOK {cash:,.0f}) — skipping", flush=True)
        return

    # Calculate position size
    n              = len(signals)
    position_size  = cash / n
    exit_date      = get_trading_days_ahead(today, HOLD_DAYS)

    print(f"[ORDERS] {n} signals — NOK {position_size:,.0f} each — exit {exit_date}", flush=True)

    new_positions = []
    total_invested = 0.0

    for s in signals:
        ticker = s["ticker"]
        price  = get_current_price(ticker)

        if not price or price <= 0:
            print(f"  [SKIP] {ticker} — no price data", flush=True)
            continue

        shares     = position_size / price
        invested   = shares * price
        stop_price = round(price * (1 - STOP_LOSS), 4)

        pos = {
            "id":          f"{ticker}_{today}",
            "ticker":      ticker,
            "name":        s.get("name", ""),
            "sector":      s.get("sector", ""),
            "entry_date":  today,
            "exit_date":   exit_date,
            "entry_price": round(price, 4),
            "stop_price":  stop_price,
            "shares":      round(shares, 6),
            "invested":    round(invested, 2),
            "div_score":   s.get("div_score"),
            "sentiment":   s.get("sentiment"),
            "status":      "open",
            "exit_price":  None,
            "pnl":         None,
            "pnl_pct":     None,
            "exit_reason": None,
        }
        new_positions.append(pos)
        total_invested += invested
        print(f"  BUY  {ticker:<12} {s['name'][:25]:<25} "
              f"NOK {price:.2f} × {shares:.2f} shares = NOK {invested:,.0f}  "
              f"stop={stop_price:.2f}", flush=True)

    if new_positions:
        portfolio["cash"] -= total_invested
        save_portfolio(portfolio)
        positions.extend(new_positions)
        save_positions(positions)
        print(f"[ORDERS] Placed {len(new_positions)} trades. "
              f"Cash remaining: NOK {portfolio['cash']:,.0f}", flush=True)
    else:
        print("[ORDERS] No valid trades placed", flush=True)


if __name__ == "__main__":
    main()

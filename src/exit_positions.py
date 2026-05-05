"""
exit_positions.py
Closes positions that have reached their exit date or hit stop loss.
Updates trade_log.csv and portfolio.json.
Runs daily at 16:20 Oslo Mon-Fri.
"""

import os, json, csv, urllib.request
from datetime import date, datetime, timezone

TURSO_URL   = os.environ["TURSO_URL"]
TURSO_TOKEN = os.environ["TURSO_TOKEN"]
DATA_DIR    = os.path.join(os.path.dirname(__file__), "..", "data")
STOP_LOSS   = 0.03  # 3%


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


def get_current_price(ticker):
    today = date.today().isoformat()
    rows = turso(f"""
        SELECT close FROM stock_prices
        WHERE ticker = '{ticker}' AND date <= '{today}'
        ORDER BY date DESC LIMIT 1
    """)
    return float(rows[0]["close"]) if rows and rows[0]["close"] else None


def load_positions():
    path = os.path.join(DATA_DIR, "positions.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return []


def save_positions(positions):
    path = os.path.join(DATA_DIR, "positions.json")
    with open(path, "w") as f:
        json.dump(positions, f, indent=2)


def load_portfolio():
    path = os.path.join(DATA_DIR, "portfolio.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"cash": 100_000.0, "start_capital": 100_000.0}


def save_portfolio(portfolio):
    path = os.path.join(DATA_DIR, "portfolio.json")
    with open(path, "w") as f:
        json.dump(portfolio, f, indent=2)


def append_trade_log(trade):
    path = os.path.join(DATA_DIR, "trade_log.csv")
    os.makedirs(DATA_DIR, exist_ok=True)
    file_exists = os.path.exists(path)
    fields = ["id", "ticker", "name", "sector", "entry_date", "exit_date",
              "entry_price", "exit_price", "stop_price", "shares", "invested",
              "pnl", "pnl_pct", "exit_reason", "div_score", "sentiment"]
    with open(path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if not file_exists:
            writer.writeheader()
        writer.writerow({k: trade.get(k, "") for k in fields})


def update_portfolio_history(portfolio_value):
    """Append today's portfolio value to history."""
    path = os.path.join(DATA_DIR, "portfolio_history.csv")
    os.makedirs(DATA_DIR, exist_ok=True)
    file_exists = os.path.exists(path)
    today = date.today().isoformat()
    with open(path, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["date", "portfolio_value", "cash"])
        writer.writerow([today, round(portfolio_value, 2),
                         round(portfolio.get("cash", 0), 2)])


def main():
    today     = date.today().isoformat()
    positions = load_positions()
    portfolio = load_portfolio()

    open_positions = [p for p in positions if p["status"] == "open"]
    print(f"[EXIT] Checking {len(open_positions)} open positions for {today}...", flush=True)

    closed_count  = 0
    total_returned = 0.0

    for pos in open_positions:
        ticker      = pos["ticker"]
        exit_date   = pos.get("exit_date")
        entry_price = float(pos["entry_price"])
        stop_price  = float(pos["stop_price"])
        shares      = float(pos["shares"])
        invested    = float(pos["invested"])

        # Get current price
        current_price = get_current_price(ticker)
        if not current_price:
            print(f"  [SKIP] {ticker} — no price data", flush=True)
            continue

        should_exit = False
        exit_reason = None

        # Stop loss check
        if current_price <= stop_price:
            should_exit = True
            exit_reason = "stop_loss"
            exit_price  = stop_price  # assume filled at stop price

        # Hold period check
        elif exit_date and today >= exit_date:
            should_exit = True
            exit_reason = "hold_period"
            exit_price  = current_price

        if should_exit:
            proceeds  = shares * exit_price
            pnl       = proceeds - invested
            pnl_pct   = round((exit_price - entry_price) / entry_price * 100, 4)

            # Cap loss at stop loss
            if exit_reason == "stop_loss":
                pnl_pct   = round(-STOP_LOSS * 100, 4)
                proceeds  = invested * (1 - STOP_LOSS)
                pnl       = proceeds - invested

            pos["status"]      = "closed"
            pos["exit_date"]   = today
            pos["exit_price"]  = round(exit_price, 4)
            pos["pnl"]         = round(pnl, 2)
            pos["pnl_pct"]     = pnl_pct
            pos["exit_reason"] = exit_reason

            portfolio["cash"] += proceeds
            total_returned    += proceeds
            closed_count      += 1

            flag = "🛑 STOP" if exit_reason == "stop_loss" else "✅ EXIT"
            print(f"  {flag} {ticker:<12} entry={entry_price:.2f}  "
                  f"exit={exit_price:.2f}  pnl={pnl_pct:+.2f}%  "
                  f"NOK {pnl:+,.0f}", flush=True)

            append_trade_log(pos)

    if closed_count > 0:
        save_positions(positions)
        save_portfolio(portfolio)

    # Calculate total portfolio value (cash + open positions at current price)
    total_value = portfolio["cash"]
    for pos in positions:
        if pos["status"] == "open":
            price = get_current_price(pos["ticker"])
            if price:
                total_value += float(pos["shares"]) * price

    start    = portfolio.get("start_capital", 100_000)
    total_pct = round((total_value - start) / start * 100, 2)

    print(f"\n[EXIT] Closed {closed_count} positions", flush=True)
    print(f"[EXIT] Portfolio value: NOK {total_value:,.0f} ({total_pct:+.2f}%)", flush=True)
    print(f"[EXIT] Cash: NOK {portfolio['cash']:,.0f}", flush=True)

    # Update history
    update_portfolio_history(total_value)


if __name__ == "__main__":
    main()

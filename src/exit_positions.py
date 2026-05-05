"""
exit_positions.py
Closes positions after hold period or stop loss.
Updates trade_log.csv, portfolio.json and portfolio_history.csv.

Runs daily at 16:20 Oslo Mon-Fri.

Environment variables:
  TURSO_URL     — Turso database URL
  TURSO_TOKEN   — Turso auth token
  LIVE_TRADING  — "true" for real SAXO trades (default: false)
  SAXO_TOKEN    — SAXO OAuth2 token (live only)
  SAXO_ACCOUNT  — SAXO account key (live only)
"""

import os, json, csv, urllib.request
from datetime import date, datetime, timezone

TURSO_URL    = os.environ["TURSO_URL"]
TURSO_TOKEN  = os.environ["TURSO_TOKEN"]
LIVE_TRADING = os.environ.get("LIVE_TRADING", "false").lower() == "true"
SAXO_TOKEN   = os.environ.get("SAXO_TOKEN", "")
SAXO_ACCOUNT = os.environ.get("SAXO_ACCOUNT", "")

DATA_DIR  = os.path.join(os.path.dirname(__file__), "..", "data")
STOP_LOSS = 0.03

SAXO_BASE = "https://gateway.saxobank.com/sim/openapi" if not LIVE_TRADING \
            else "https://gateway.saxobank.com/openapi"


# ── Turso ─────────────────────────────────────────────────────────────────────

def turso(sql):
    url = TURSO_URL.replace("libsql://", "https://") + "/v2/pipeline"
    req = urllib.request.Request(
        url,
        data=json.dumps({"requests": [{"type": "execute", "stmt": {"sql": sql}},
                                       {"type": "close"}]}).encode(),
        headers={"Authorization": f"Bearer {TURSO_TOKEN}",
                 "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    result = data["results"][0]["response"]["result"]
    cols   = [c["name"] for c in result["cols"]]
    return [dict(zip(cols, [v.get("value") for v in row])) for row in result["rows"]]


# ── Price fetching ────────────────────────────────────────────────────────────

def get_price_paper(ticker):
    """Paper: latest closing price from Turso."""
    today = date.today().isoformat()
    rows  = turso(f"""
        SELECT close FROM stock_prices
        WHERE ticker = '{ticker}' AND date <= '{today}'
        ORDER BY date DESC LIMIT 1
    """)
    return float(rows[0]["close"]) if rows and rows[0]["close"] else None


def get_saxo_uic(ticker):
    """Look up SAXO UIC from ticker symbol."""
    uic_map = {
        # "EQNR.OL": "2281",
    }
    if ticker in uic_map:
        return uic_map[ticker]
    symbol = ticker.replace(".OL", "")
    try:
        req = urllib.request.Request(
            f"{SAXO_BASE}/ref/v1/instruments?Keywords={symbol}&ExchangeId=OSE&AssetTypes=Stock",
            headers={"Authorization": f"Bearer {SAXO_TOKEN}"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        instruments = data.get("Data", [])
        if instruments:
            return str(instruments[0]["Identifier"])
    except Exception as e:
        print(f"  [SAXO] UIC lookup failed for {ticker}: {e}", flush=True)
    return None


def get_price_live(ticker):
    """Live: real-time bid price from SAXO (what you receive when selling)."""
    if not SAXO_TOKEN:
        raise RuntimeError("SAXO_TOKEN not set")
    uic = get_saxo_uic(ticker)
    if not uic:
        return None
    try:
        req = urllib.request.Request(
            f"{SAXO_BASE}/trade/v1/infoprices?Uic={uic}&AssetType=Stock",
            headers={"Authorization": f"Bearer {SAXO_TOKEN}"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        bid = data.get("Quote", {}).get("Bid")
        return float(bid) if bid else None
    except Exception as e:
        print(f"  [SAXO] Price fetch failed for {ticker}: {e}", flush=True)
        return None


def get_current_price(ticker):
    if LIVE_TRADING:
        return get_price_live(ticker)
    return get_price_paper(ticker)


# ── SAXO sell order ───────────────────────────────────────────────────────────

def place_saxo_sell(ticker, shares, saxo_order_id=None):
    """
    Place a real market sell order via SAXO API.
    Returns order ID if successful.
    """
    if not SAXO_TOKEN or not SAXO_ACCOUNT:
        raise RuntimeError("SAXO_TOKEN and SAXO_ACCOUNT required")

    uic = get_saxo_uic(ticker)
    if not uic:
        return None

    order = {
        "Uic":         uic,
        "AssetType":   "Stock",
        "BuySell":     "Sell",
        "Amount":      round(shares, 0),
        "OrderType":   "Market",
        "ManualOrder": False,
        "AccountKey":  SAXO_ACCOUNT,
    }
    try:
        req = urllib.request.Request(
            f"{SAXO_BASE}/trade/v2/orders",
            data=json.dumps(order).encode(),
            headers={"Authorization": f"Bearer {SAXO_TOKEN}",
                     "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        order_id = data.get("OrderId")
        print(f"  [SAXO] Sell order placed: {ticker} — OrderId={order_id}", flush=True)
        return order_id
    except Exception as e:
        print(f"  [SAXO] Sell failed for {ticker}: {e}", flush=True)
        return None


# ── File helpers ──────────────────────────────────────────────────────────────

def load_positions():
    path = os.path.join(DATA_DIR, "positions.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return []


def save_positions(p):
    path = os.path.join(DATA_DIR, "positions.json")
    with open(path, "w") as f:
        json.dump(p, f, indent=2)


def load_portfolio():
    path = os.path.join(DATA_DIR, "portfolio.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"cash": 100_000.0, "start_capital": 100_000.0}


def save_portfolio(p):
    path = os.path.join(DATA_DIR, "portfolio.json")
    with open(path, "w") as f:
        json.dump(p, f, indent=2)


def append_trade_log(trade):
    path        = os.path.join(DATA_DIR, "trade_log.csv")
    file_exists = os.path.exists(path)
    fields      = ["id", "ticker", "name", "sector", "entry_date", "exit_date",
                   "entry_price", "exit_price", "stop_price", "shares", "invested",
                   "pnl", "pnl_pct", "exit_reason", "div_score", "sentiment",
                   "live", "saxo_order_id", "price_source"]
    with open(path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if not file_exists:
            writer.writeheader()
        writer.writerow({k: trade.get(k, "") for k in fields})


def append_portfolio_history(total_value, cash):
    path        = os.path.join(DATA_DIR, "portfolio_history.csv")
    file_exists = os.path.exists(path)
    today       = date.today().isoformat()
    with open(path, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["date", "portfolio_value", "cash"])
        writer.writerow([today, round(total_value, 2), round(cash, 2)])


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    today     = date.today().isoformat()
    mode      = "LIVE 🔴" if LIVE_TRADING else "PAPER 📄"
    positions = load_positions()
    portfolio = load_portfolio()

    open_pos = [p for p in positions if p["status"] == "open"]
    print(f"[EXIT] {mode} — {today} — {len(open_pos)} open positions", flush=True)

    closed_count = 0

    for pos in open_pos:
        ticker      = pos["ticker"]
        exit_date   = pos.get("exit_date")
        entry_price = float(pos["entry_price"])
        stop_price  = float(pos["stop_price"])
        shares      = float(pos["shares"])
        invested    = float(pos["invested"])

        current_price = get_current_price(ticker)
        if not current_price:
            print(f"  [SKIP] {ticker} — no price", flush=True)
            continue

        should_exit = False
        exit_reason = None
        exit_price  = current_price

        # Stop loss — price has fallen below stop
        if current_price <= stop_price:
            should_exit = True
            exit_reason = "stop_loss"
            # In paper trading: assume filled at stop price
            # In live trading: filled at market (could be worse on gap down)
            exit_price  = stop_price if not LIVE_TRADING else current_price

        # Hold period expired
        elif exit_date and today >= exit_date:
            should_exit = True
            exit_reason = "hold_period"
            exit_price  = current_price

        if should_exit:
            # Place real SAXO sell order if live
            if LIVE_TRADING:
                sell_order_id = place_saxo_sell(ticker, shares, pos.get("saxo_order_id"))
                pos["saxo_sell_order_id"] = sell_order_id

            # Calculate P&L
            if exit_reason == "stop_loss" and not LIVE_TRADING:
                # Paper: cap loss cleanly at stop loss %
                pnl_pct  = round(-STOP_LOSS * 100, 4)
                proceeds = invested * (1 - STOP_LOSS)
                pnl      = proceeds - invested
            else:
                pnl_pct  = round((exit_price - entry_price) / entry_price * 100, 4)
                proceeds = shares * exit_price
                pnl      = proceeds - invested

            pos["status"]      = "closed"
            pos["exit_date"]   = today
            pos["exit_price"]  = round(exit_price, 4)
            pos["pnl"]         = round(pnl, 2)
            pos["pnl_pct"]     = pnl_pct
            pos["exit_reason"] = exit_reason

            portfolio["cash"] += proceeds
            closed_count      += 1

            flag = "🛑 STOP" if exit_reason == "stop_loss" else "✅ EXIT"
            print(f"  {flag} {ticker:<12} {pos['name'][:24]:<24} "
                  f"entry={entry_price:.2f} exit={exit_price:.2f} "
                  f"pnl={pnl_pct:+.2f}% NOK {pnl:+,.0f}", flush=True)

            append_trade_log(pos)

    if closed_count > 0:
        save_positions(positions)
        save_portfolio(portfolio)

    # Calculate total portfolio value
    total_value = portfolio["cash"]
    for pos in positions:
        if pos["status"] == "open":
            price = get_current_price(pos["ticker"])
            if price:
                total_value += float(pos["shares"]) * price

    start     = portfolio.get("start_capital", 100_000)
    total_pct = round((total_value - start) / start * 100, 2)

    print(f"\n[EXIT] Closed: {closed_count} | "
          f"Portfolio: NOK {total_value:,.0f} ({total_pct:+.2f}%) | "
          f"Cash: NOK {portfolio['cash']:,.0f}", flush=True)

    append_portfolio_history(total_value, portfolio["cash"])


if __name__ == "__main__":
    main()

"""
signal_reader.py
Reads tonight's vs_sector bullish signals from Turso.
Saves to data/signals_tonight.json
Runs nightly after sentiment pipeline (~23:00 Oslo).
"""

import os, json, urllib.request
from datetime import date, datetime, timezone

TURSO_URL   = os.environ["TURSO_URL"]
TURSO_TOKEN = os.environ["TURSO_TOKEN"]
THRESHOLD   = 0.25
DATA_DIR    = os.path.join(os.path.dirname(__file__), "..", "data")


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


def main():
    today = date.today().isoformat()
    print(f"[SIGNAL] Reading signals for {today}...", flush=True)

    signals = turso(f"""
        WITH sa AS (
            SELECT s.date, c.sector, AVG(s.final_compound) AS avg
            FROM spillover_scores s
            JOIN companies c ON c.ticker = s.ticker
            GROUP BY s.date, c.sector
        )
        SELECT s.ticker, c.name, c.sector,
               ROUND(s.final_compound, 4)          AS sentiment,
               ROUND(s.final_compound - sa.avg, 4) AS div_score,
               ROUND(p.close, 4)                   AS price
        FROM spillover_scores s
        JOIN companies c ON c.ticker = s.ticker
        JOIN sa ON sa.date = s.date AND sa.sector = c.sector
        LEFT JOIN stock_prices p
            ON p.ticker = s.ticker AND p.date = s.date
        WHERE s.date = '{today}'
          AND (s.final_compound - sa.avg) > {THRESHOLD}
          AND c.active = 1
        ORDER BY (s.final_compound - sa.avg) DESC
    """)

    print(f"[SIGNAL] Found {len(signals)} signals", flush=True)
    for s in signals:
        print(f"  {s['ticker']:<12} {s['name'][:28]:<28} div={s['div_score']:+.3f}  price={s['price']}", flush=True)

    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, "signals_tonight.json")
    with open(path, "w") as f:
        json.dump({
            "date":      today,
            "generated": datetime.now(timezone.utc).isoformat(),
            "threshold": THRESHOLD,
            "count":     len(signals),
            "signals":   signals,
        }, f, indent=2)

    print(f"[SIGNAL] Saved to {path}", flush=True)


if __name__ == "__main__":
    main()

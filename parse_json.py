import json

log_file = "logs/deepseek_trader_2026-05-27_002731:811.json"
trades = []
with open(log_file, "r") as f:
    for line in f:
        try:
            data = json.loads(line)
            if data.get("signal") in ["BUY", "SELL"] and not data.get("is_fallback"):
                trades.append(data)
        except:
            continue

print(f"Found {len(trades)} signals in {log_file}")
for t in trades[-10:]:
    print(f"Signal: {t.get('signal')}, Conf: {t.get('confidence')}, Px: {t.get('price')}")
    print(f"Reason: {t.get('reason')}")
    print("-" * 40)

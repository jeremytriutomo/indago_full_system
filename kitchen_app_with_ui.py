from flask import Flask, jsonify, request, render_template_string
import sqlite3
import requests
from datetime import datetime
from collections import defaultdict

# =========================
# CONFIGURATION
# =========================

DB_PATH = "indago_kitchen.db"

ORDER_SERVICE_URL = "http://localhost:5001/orders-weekly"
INVENTORY_STOCK_ENDPOINT = "http://localhost:5002/stock"

# Recipes (Bill of Materials) — base units only
RECIPES = {
    "capucino": [
        {"item": "beans", "qty_per_unit": 10, "unit": "g"},
        {"item": "milk", "qty_per_unit": 150, "unit": "ml"},
    ],
    "Latte": [
        {"item": "beans", "qty_per_unit": 8, "unit": "g"},
        {"item": "milk", "qty_per_unit": 200, "unit": "ml"},
    ],
}

# 🔥 Optional boot date
BOOT_DATE = "2025-12-12"

# =========================
# APP INIT
# =========================

app = Flask(__name__)

# =========================
# DATABASE
# =========================

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()

    # Append-only production log
    c.execute("""
              
        CREATE TABLE IF NOT EXISTS batch_consumption (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            production_date TEXT NOT NULL,
            item TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            unit TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()

# =========================
# PRODUCTION LOGIC
# =========================

def start_production(production_date: str):
    """
    Executes production for a specific date:
    1. Fetch weekly orders
    2. Filter orders by production_date
    3. Calculate required raw materials
    4. Check inventory stock
    5. Append production output

    Returns a JSON-serializable dict (so UI/API can display it).
    Raises RuntimeError for insufficient stock (caught by API route).
    """

    # -------- Fetch orders --------
    order_resp = requests.get(ORDER_SERVICE_URL, timeout=5)
    order_resp.raise_for_status()
    orders = order_resp.json().get("orders", [])

    # -------- Filter orders for this date --------
    daily_orders = [o for o in orders if o.get("date") == production_date]

    if not daily_orders:
        return {
            "status": "no_orders",
            "message": f"No orders for {production_date}",
            "date": production_date,
            "required": {},
            "inserted_rows": 0,
        }

    # -------- Calculate required materials --------
    required = defaultdict(lambda: {"quantity": 0, "unit": None})

    skipped_products = []
    for order in daily_orders:
        product = order.get("product")
        qty = int(order.get("quantity", 0))

        recipe = RECIPES.get(product)
        if not recipe:
            skipped_products.append(product)
            continue

        for r in recipe:
            required[r["item"]]["quantity"] += qty * r["qty_per_unit"]
            required[r["item"]]["unit"] = r["unit"]

    required_dict = {k: v for k, v in required.items()}

    if not required_dict:
        return {
            "status": "no_recipes_matched",
            "message": f"Orders exist for {production_date} but no recipes matched products.",
            "date": production_date,
            "skipped_products": skipped_products,
            "required": {},
            "inserted_rows": 0,
        }

    # -------- Fetch inventory stock --------
    stock_resp = requests.get(INVENTORY_STOCK_ENDPOINT, timeout=5)
    stock_resp.raise_for_status()

    stock_lookup = {s["item"]: s["quantity"] for s in stock_resp.json().get("stock", [])}

    # -------- Validate stock --------
    insufficient = []
    for item, req in required.items():
        available = int(stock_lookup.get(item, 0))
        if available < req["quantity"]:
            insufficient.append({
                "item": item,
                "required": req["quantity"],
                "available": available,
                "unit": req["unit"]
            })

    if insufficient:
        # Raise an error with structured details
        raise RuntimeError({
            "message": "Insufficient stock for production",
            "date": production_date,
            "details": insufficient,
            "required": required_dict,
        })

    # -------- Append production output --------
    conn = get_db()
    c = conn.cursor()

    inserted = 0
    for item, req in required.items():
        c.execute("""
            INSERT INTO batch_consumption
            (production_date, item, quantity, unit, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (
            production_date,
            item,
            req["quantity"],
            req["unit"],
            datetime.utcnow().isoformat()
        ))
        inserted += 1

    conn.commit()
    conn.close()

    return {
        "status": "success",
        "message": f"Production completed for {production_date}",
        "date": production_date,
        "required": required_dict,
        "inserted_rows": inserted,
        "skipped_products": skipped_products,
    }

# =========================
# UI (Simple HTML Page)
# =========================

UI_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Kitchen Production UI</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 24px; max-width: 900px; }
    .row { display: flex; gap: 12px; align-items: center; }
    input { padding: 8px; }
    button { padding: 8px 12px; cursor: pointer; }
    pre { background: #f5f5f5; padding: 12px; border-radius: 8px; overflow: auto; }
    .hint { color: #666; font-size: 14px; }
  </style>
</head>
<body>
  <h2>Kitchen Production</h2>
  <div class="row">
    <label for="date">Production date:</label>
    <input id="date" type="date" />
    <button id="runBtn">Start Production</button>
    <button id="loadBatchBtn">Load Batch Consumption</button>
  </div>
  <p class="hint">
    Start Production will call <code>POST /start-production</code>. Load Batch calls <code>GET /batch?date=YYYY-MM-DD</code>.
  </p>

  <h3>Response</h3>
  <pre id="output">Ready.</pre>

<script>
  const output = document.getElementById('output');
  const dateInput = document.getElementById('date');

  // default date to today
  dateInput.valueAsDate = new Date();

  document.getElementById('runBtn').addEventListener('click', async () => {
    const date = dateInput.value;
    output.textContent = 'Running...';

    try {
      const res = await fetch('/start-production', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ date })
      });
      const data = await res.json();
      output.textContent = JSON.stringify(data, null, 2);
    } catch (err) {
      output.textContent = 'Error: ' + err;
    }
  });

  document.getElementById('loadBatchBtn').addEventListener('click', async () => {
    const date = dateInput.value;
    output.textContent = 'Loading batch...';

    try {
      const res = await fetch(`/batch?date=${encodeURIComponent(date)}`);
      const data = await res.json();
      output.textContent = JSON.stringify(data, null, 2);
    } catch (err) {
      output.textContent = 'Error: ' + err;
    }
  });
</script>
</body>
</html>
"""

@app.route("/ui", methods=["GET"])
def ui():
    return render_template_string(UI_HTML)

# =========================
# API
# =========================

@app.route("/start-production", methods=["POST"])
def start_production_route():
    body = request.get_json(silent=True) or {}
    production_date = body.get("date")

    if not production_date:
        return jsonify({"error": "date is required (YYYY-MM-DD)"}), 400

    try:
        result = start_production(production_date)
        return jsonify(result), 200
    except requests.HTTPError as exc:
        # Upstream service returned an error
        return jsonify({
            "status": "error",
            "message": "Upstream service error",
            "details": str(exc),
        }), 502
    except requests.RequestException as exc:
        # Network/timeout issues
        return jsonify({
            "status": "error",
            "message": "Network error calling upstream services",
            "details": str(exc),
        }), 502
    except RuntimeError as exc:
        # Our own structured runtime errors
        details = exc.args[0] if exc.args else {"message": "Runtime error"}
        return jsonify({
            "status": "error",
            "error": details,
        }), 400
    except Exception as exc:
        return jsonify({
            "status": "error",
            "message": "Unexpected error",
            "details": str(exc),
        }), 500


@app.route("/batch", methods=["GET"])
def get_batch():
    production_date = request.args.get("date")
    if not production_date:
        return jsonify({"error": "date is required (YYYY-MM-DD)"}), 400

    conn = get_db()
    c = conn.cursor()

    c.execute("""
        SELECT production_date, item, quantity, unit
        FROM batch_consumption
        WHERE production_date = ?
        ORDER BY item
    """, (production_date,))

    rows = c.fetchall()
    conn.close()

    return jsonify({
        "date": production_date,
        "consumption": [dict(row) for row in rows]
    })


@app.route("/", methods=["GET"])
def health():
    return jsonify({"message": "Kitchen subsystem running"})

# =========================
# STARTUP
# =========================

if __name__ == "__main__":
    init_db()

    # Optional: keep this OFF so production is triggered only via UI/button.
    # If you still want boot-time production, uncomment the next 2 lines.
    # try:
    #     start_production(BOOT_DATE)
    # except Exception as e:
    #     print("Boot production failed:", e)

    app.run(port=5004, debug=True)

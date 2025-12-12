from flask import Flask, jsonify, request
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

# 🔥 PRODUCTION DATE (BOOT TIME)
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


def start_production(production_date):
    """
    Executes production for a specific date:
    1. Fetch weekly orders
    2. Filter orders by production_date
    3. Calculate required raw materials
    4. Check inventory stock
    5. Append production output
    """

    # -------- Fetch orders --------
    order_resp = requests.get(
        ORDER_SERVICE_URL,
        timeout=5
    )
    order_resp.raise_for_status()
    orders = order_resp.json().get("orders", [])

    # -------- Filter orders for this date --------
    daily_orders = [
        o for o in orders if o["date"] == production_date
    ]

    if not daily_orders:
        print(f"No orders for {production_date}")
        return

    # -------- Calculate required materials --------
    required = defaultdict(lambda: {"quantity": 0, "unit": None})

    for order in daily_orders:
        product = order["product"]
        qty = order["quantity"]

        recipe = RECIPES.get(product)
        if not recipe:
            continue

        for r in recipe:
            required[r["item"]]["quantity"] += qty * r["qty_per_unit"]
            required[r["item"]]["unit"] = r["unit"]

    # -------- Fetch inventory stock --------
    stock_resp = requests.get(INVENTORY_STOCK_ENDPOINT, timeout=5)
    stock_resp.raise_for_status()

    stock_lookup = {
        s["item"]: s["quantity"]
        for s in stock_resp.json().get("stock", [])
    }

    # -------- Validate stock --------
    insufficient = []

    for item, req in required.items():
        available = stock_lookup.get(item, 0)
        if available < req["quantity"]:
            insufficient.append({
                "item": item,
                "required": req["quantity"],
                "available": available,
                "unit": req["unit"]
            })

    if insufficient:
        raise RuntimeError({
            "message": "Insufficient stock for production",
            "details": insufficient
        })

    # -------- Append production output --------
    conn = get_db()
    c = conn.cursor()

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

    conn.commit()
    conn.close()

    print(f"Production completed for {production_date}")

# =========================
# API
# =========================


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
    start_production(BOOT_DATE)   # 🔥 DATE-BASED PRODUCTION
    app.run(port=5004, debug=True)

from datetime import datetime
import json
import sqlite3
import requests

from flask import Flask, jsonify, request
from flask_cors import CORS

# =========================
# APP CONFIG
# =========================

app = Flask(__name__)
CORS(app)

DB_PATH = "indago_inventory.db"

SEED_INVENTORY = {
    "beans": 10000,
    "milk": 100000,
}

ITEM_UNITS = {
    "beans": "g",
    "milk": "ml",
}

LOW_STOCK_THRESHOLD = 0.1

FINANCE_PURCHASE_ENDPOINT = "http://localhost:5003/PurchaseRequest"
KITCHEN_BATCH_ENDPOINT = "http://localhost:5004/batch"

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

    # Inventory table
    c.execute("""
        CREATE TABLE IF NOT EXISTS inventory (
            item TEXT PRIMARY KEY,
            quantity INTEGER NOT NULL
        )
    """)

    # Procurement log
    c.execute("""
        CREATE TABLE IF NOT EXISTS procurement_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT,
            item_name TEXT,
            quantity_needed INTEGER,
            unit TEXT,
            status TEXT,
            payload TEXT,
            response TEXT,
            created_at TEXT
        )
    """)

    # Seed inventory
    for item, qty in SEED_INVENTORY.items():
        c.execute(
            "INSERT OR IGNORE INTO inventory (item, quantity) VALUES (?, ?)",
            (item, qty)
        )

    conn.commit()
    conn.close()

# =========================
# PROCUREMENT LOGIC
# =========================


def should_trigger_purchase(item, remaining):
    baseline = SEED_INVENTORY.get(item)
    return baseline is not None and remaining <= baseline * LOW_STOCK_THRESHOLD


def calculate_replenishment_quantity(item, remaining):
    baseline = SEED_INVENTORY.get(item, 0)
    return max(baseline - remaining, int(baseline * 0.5))


def has_open_procurement(cursor, item):
    cursor.execute("""
        SELECT 1 FROM procurement_log
        WHERE item_name = ? AND status IN ('pending', 'submitted')
        ORDER BY created_at DESC
        LIMIT 1
    """, (item,))
    return cursor.fetchone() is not None


def trigger_purchase_request(cursor, item, remaining):
    if has_open_procurement(cursor, item):
        return

    payload = {
        "order_id": f"PR-{item}-{int(datetime.utcnow().timestamp())}",
        "item_name": item,
        "quantity_needed": calculate_replenishment_quantity(item, remaining),
        "unit": ITEM_UNITS.get(item),
        "current_stock": remaining,
    }

    status = "pending"
    response_body = None

    try:
        resp = requests.post(FINANCE_PURCHASE_ENDPOINT,
                             json=payload, timeout=5)
        response_body = resp.text
        resp.raise_for_status()
    except Exception as e:
        status = "failed"
        response_body = str(e)

    cursor.execute("""
        INSERT INTO procurement_log
        (order_id, item_name, quantity_needed, unit, status, payload, response, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        payload["order_id"],
        item,
        payload["quantity_needed"],
        payload["unit"],
        status,
        json.dumps(payload),
        response_body,
        datetime.utcnow().isoformat()
    ))

# =========================
# STOCK CONSUMPTION
# =========================


def apply_consumption(consumption):
    conn = get_db()
    c = conn.cursor()

    for row in consumption:
        item = row["item"]
        qty = int(row["quantity"])

        c.execute("SELECT quantity FROM inventory WHERE item = ?", (item,))
        current = c.fetchone()["quantity"]

        new_qty = max(current - qty, 0)
        c.execute("UPDATE inventory SET quantity = ? WHERE item = ?",
                  (new_qty, item))

        if should_trigger_purchase(item, new_qty):
            trigger_purchase_request(c, item, new_qty)

    conn.commit()
    conn.close()

# =========================
# API
# =========================


@app.route("/stock", methods=["GET"])
def get_stock():
    conn = get_db()
    c = conn.cursor()

    c.execute("SELECT item, quantity FROM inventory")
    stock = [
        {
            "item": r["item"],
            "quantity": r["quantity"],
            "unit": ITEM_UNITS.get(r["item"])
        }
        for r in c.fetchall()
    ]

    conn.close()
    return jsonify({"stock": stock})


@app.route("/consume", methods=["POST"])
def consume_from_kitchen():
    """
    Inventory pulls batch consumption from Kitchen and applies it.
    """
    date = request.args.get("date")
    if not date:
        return jsonify({"error": "date is required"}), 400

    resp = requests.get(KITCHEN_BATCH_ENDPOINT, params={
                        "date": date}, timeout=5)
    resp.raise_for_status()

    consumption = resp.json().get("consumption", [])
    apply_consumption(consumption)

    return jsonify({
        "message": f"Stock updated from kitchen batch for {date}",
        "items_consumed": consumption
    })


@app.route("/", methods=["GET"])
def health():
    return jsonify({"message": "Inventory subsystem running"})

# =========================
# STARTUP
# =========================


if __name__ == "__main__":
    init_db()
    app.run(port=5002, debug=True)

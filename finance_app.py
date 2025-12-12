import sqlite3
from flask import Flask, request, jsonify
from datetime import datetime
import os

# =========================
# CONFIGURATION
# =========================

app = Flask(__name__)

DB_PATH = "indago_financial_records.db"

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

    c.execute("""
        CREATE TABLE IF NOT EXISTS purchase_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT NOT NULL,
            item_name TEXT NOT NULL,
            quantity_needed INTEGER NOT NULL,
            unit TEXT,
            current_stock INTEGER,
            estimated_cost REAL NOT NULL,
            status TEXT NOT NULL,
            decision_note TEXT,
            request_date TEXT NOT NULL,
            decision_date TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()

# =========================
# BUSINESS LOGIC
# =========================


def evaluate_purchase_request(estimated_cost):
    """
    Simple approval logic:
    - APPROVE if cost <= 500,000
    - REJECT otherwise
    """
    BUDGET_LIMIT = 500_000

    if estimated_cost <= BUDGET_LIMIT:
        return "APPROVED", "Auto-approved: within budget limit."
    else:
        return "REJECTED", f"Auto-rejected: exceeds budget limit ({BUDGET_LIMIT})."

# =========================
# API ROUTES
# =========================


@app.route("/", methods=["GET"])
def health():
    return jsonify({"message": "Finance subsystem running"})


@app.route("/PurchaseRequest", methods=["POST"])
def process_purchase_request():
    """
    Called by Inventory subsystem
    """
    data = request.json

    required_fields = [
        "order_id",
        "item_name",
        "quantity_needed",
        "estimated_cost"
    ]

    if not data or not all(field in data for field in required_fields):
        return jsonify({"error": "Incomplete purchase request data"}), 400

    status, note = evaluate_purchase_request(data["estimated_cost"])

    now = datetime.utcnow().isoformat()

    conn = get_db()
    c = conn.cursor()

    c.execute("""
        INSERT INTO purchase_requests (
            order_id,
            item_name,
            quantity_needed,
            unit,
            current_stock,
            estimated_cost,
            status,
            decision_note,
            request_date,
            decision_date
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data["order_id"],
        data["item_name"],
        data["quantity_needed"],
        data.get("unit"),
        data.get("current_stock"),
        data["estimated_cost"],
        status,
        note,
        now,
        now
    ))

    conn.commit()

    request_id = c.lastrowid
    conn.close()

    return jsonify({
        "message": f"Purchase request {status}",
        "data": {
            "id": request_id,
            "order_id": data["order_id"],
            "item_name": data["item_name"],
            "quantity_needed": data["quantity_needed"],
            "estimated_cost": data["estimated_cost"],
            "status": status,
            "decision_note": note
        }
    }), 201 if status == "APPROVED" else 403


@app.route("/finance/history", methods=["GET"])
def finance_history():
    """
    Optional audit trail
    """
    conn = get_db()
    c = conn.cursor()

    c.execute("""
        SELECT
            id,
            order_id,
            item_name,
            quantity_needed,
            estimated_cost,
            status,
            decision_note,
            request_date,
            decision_date
        FROM purchase_requests
        ORDER BY request_date DESC
    """)

    rows = c.fetchall()
    conn.close()

    return jsonify([
        dict(row) for row in rows
    ])

# =========================
# STARTUP
# =========================


if __name__ == "__main__":
    init_db()
    app.run(port=5003, debug=True)

from flask import Flask, request, jsonify, render_template_string, redirect, url_for
import sqlite3
from datetime import datetime

app = Flask(__name__)

INDIVIDUAL_DB = "indago_individual_orders.db"
WEEKLY_DB = "indago_weekly_orders.db"

# =========================
# DATABASE HELPERS
# =========================


def get_db(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_dbs():
    # Individual Orders
    conn = get_db(INDIVIDUAL_DB)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS individual_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_date TEXT,
            product TEXT,
            quantity INTEGER,
            unit_price INTEGER,
            total_price INTEGER,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()

    # Weekly Orders
    conn = get_db(WEEKLY_DB)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS weekly_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_date TEXT,
            product TEXT,
            quantity INTEGER,
            total_price INTEGER,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()

# =========================
# AGGREGATION LOGIC
# =========================


def aggregate_orders():
    src = get_db(INDIVIDUAL_DB)
    dst = get_db(WEEKLY_DB)

    sc = src.cursor()
    dc = dst.cursor()

    dc.execute("DELETE FROM weekly_orders")

    sc.execute("""
        SELECT
            order_date,
            product,
            SUM(quantity) AS qty,
            SUM(total_price) AS total
        FROM individual_orders
        GROUP BY order_date, product
        ORDER BY order_date
    """)

    for row in sc.fetchall():
        dc.execute("""
            INSERT INTO weekly_orders
            (order_date, product, quantity, total_price, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (
            row["order_date"],
            row["product"],
            row["qty"],
            row["total"],
            datetime.utcnow().isoformat()
        ))

    dst.commit()
    src.close()
    dst.close()

# =========================
# UI (SIMPLE HTML)
# =========================


HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Order Input</title>
    <style>
        body { font-family: Arial; margin: 40px; }
        input, button { padding: 8px; margin: 5px; }
        table { border-collapse: collapse; margin-top: 20px; }
        th, td { border: 1px solid #ccc; padding: 8px; }
        th { background: #eee; }
    </style>
</head>
<body>

<h2>Input Individual Order</h2>

<form method="POST" action="/add-order">
    Date: <input type="date" name="order_date" required>
    Product: <input type="text" name="product" required>
    Quantity: <input type="number" name="quantity" required>
    Unit Price: <input type="number" name="unit_price" required>
    <button type="submit">Save Order</button>
</form>

<form method="POST" action="/aggregate">
    <button type="submit">Aggregate to Weekly Orders</button>
</form>

<h2>Weekly Orders</h2>
<table>
<tr>
    <th>Date</th>
    <th>Product</th>
    <th>Quantity</th>
    <th>Total Price</th>
</tr>
{% for o in weekly %}
<tr>
    <td>{{ o.order_date }}</td>
    <td>{{ o.product }}</td>
    <td>{{ o.quantity }}</td>
    <td>{{ o.total_price }}</td>
</tr>
{% endfor %}
</table>

</body>
</html>
"""

# =========================
# ROUTES
# =========================


@app.route("/", methods=["GET"])
def home():
    conn = get_db(WEEKLY_DB)
    c = conn.cursor()
    c.execute("SELECT * FROM weekly_orders ORDER BY order_date")
    weekly = c.fetchall()
    conn.close()

    return render_template_string(HTML, weekly=weekly)


@app.route("/add-order", methods=["POST"])
def add_order():
    date = request.form["order_date"]
    product = request.form["product"]
    qty = int(request.form["quantity"])
    price = int(request.form["unit_price"])

    conn = get_db(INDIVIDUAL_DB)
    c = conn.cursor()

    c.execute("""
        INSERT INTO individual_orders
        (order_date, product, quantity, unit_price, total_price, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        date,
        product,
        qty,
        price,
        qty * price,
        datetime.utcnow().isoformat()
    ))

    conn.commit()
    conn.close()

    return redirect(url_for("home"))


@app.route("/aggregate", methods=["POST"])
def aggregate():
    aggregate_orders()
    return redirect(url_for("home"))


@app.route("/orders-weekly", methods=["GET"])
def orders_weekly_api():
    conn = get_db(WEEKLY_DB)
    c = conn.cursor()

    c.execute("""
        SELECT order_date, product, quantity, total_price
        FROM weekly_orders
        ORDER BY order_date
    """)

    orders = [
        {
            "date": r["order_date"],
            "product": r["product"],
            "quantity": r["quantity"],
            "total_price": r["total_price"]
        }
        for r in c.fetchall()
    ]

    conn.close()

    return jsonify({"orders": orders})


# =========================
# STARTUP
# =========================

if __name__ == "__main__":
    init_dbs()
    app.run(port=5001, debug=True)

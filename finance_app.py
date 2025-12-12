import sqlite3
from flask import Flask, request, jsonify, render_template_string
from datetime import datetime
import json
import math
import requests
from collections import Counter

# =========================
# CONFIGURATION
# =========================

app = Flask(__name__)

FINANCE_DB_PATH = "indago_financial_records.db"
REQUEST_LOG_DB_PATH = "indago_request_log.db"
SALES_LOG_DB_PATH = "indago_sales_log.db"

# Order app endpoint (you requested GET /weekly-order)
ORDER_APP_BASE = "http://localhost:5001"
ORDER_WEEKLY_ENDPOINT = f"{ORDER_APP_BASE}/weekly-order"
ORDER_WEEKLY_FALLBACK = f"{ORDER_APP_BASE}/orders-weekly"

# =========================
# DATABASE HELPERS
# =========================


def get_db(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_finance_db():
    conn = get_db(FINANCE_DB_PATH)
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


def init_request_log_db():
    conn = get_db(REQUEST_LOG_DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS request_purchase_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            received_at TEXT NOT NULL,
            order_id TEXT,
            item_name TEXT,
            quantity_needed INTEGER,
            unit TEXT,
            current_stock INTEGER,
            estimated_cost REAL,
            status TEXT,
            decision_note TEXT,
            http_status INTEGER,
            payload TEXT
        )
    """)
    conn.commit()
    conn.close()


def init_sales_log_db():
    conn = get_db(SALES_LOG_DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS weekly_sales_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scored_at TEXT NOT NULL,

            week_start TEXT,
            week_end TEXT,

            total_orders INTEGER NOT NULL,
            total_units INTEGER NOT NULL,
            total_revenue REAL NOT NULL,
            avg_order_value REAL NOT NULL,
            top_product TEXT,
            sales_score REAL NOT NULL,

            source_endpoint TEXT,
            payload TEXT
        )
    """)
    conn.commit()
    conn.close()

# =========================
# BUSINESS LOGIC (FINANCE)
# =========================


def evaluate_purchase_request(estimated_cost):
    BUDGET_LIMIT = 500_000
    if estimated_cost <= BUDGET_LIMIT:
        return "APPROVED", "Auto-approved: within budget limit."
    return "REJECTED", f"Auto-rejected: exceeds budget limit ({BUDGET_LIMIT})."


def log_request_purchase(data, status, note, http_status):
    now = datetime.utcnow().isoformat()

    conn = get_db(REQUEST_LOG_DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO request_purchase_log (
            received_at, order_id, item_name, quantity_needed, unit,
            current_stock, estimated_cost, status, decision_note, http_status, payload
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        now,
        (data or {}).get("order_id"),
        (data or {}).get("item_name"),
        (data or {}).get("quantity_needed"),
        (data or {}).get("unit"),
        (data or {}).get("current_stock"),
        (data or {}).get("estimated_cost"),
        status,
        note,
        http_status,
        json.dumps(data or {}, ensure_ascii=False)
    ))
    conn.commit()
    conn.close()

# =========================
# SALES SCORING LOGIC
# =========================


def fetch_weekly_orders():
    """
    Tries GET /weekly-order first (as requested).
    Falls back to GET /orders-weekly for compatibility.
    Returns: (source_endpoint, json_data)
    """
    try:
        r = requests.get(ORDER_WEEKLY_ENDPOINT, timeout=5)
        r.raise_for_status()
        return ORDER_WEEKLY_ENDPOINT, r.json()
    except Exception:
        r = requests.get(ORDER_WEEKLY_FALLBACK, timeout=5)
        r.raise_for_status()
        return ORDER_WEEKLY_FALLBACK, r.json()


def normalize_orders_payload(data: dict):
    """
    Accepts multiple possible shapes and returns a list of orders.
    We try these keys: "orders", "weekly_orders", "data"
    """
    if not isinstance(data, dict):
        return []
    for key in ("orders", "weekly_orders", "data"):
        if isinstance(data.get(key), list):
            return data[key]
    # if the API directly returns a list (rare), caller will handle it; here we keep dict-only.
    return []


def compute_sales_metrics(orders: list):
    """
    Expects each order to have at least:
      - product
      - quantity
    Optional:
      - total_amount OR (price and quantity)
    Also tries to infer week_start/week_end if present.
    """
    total_orders = len(orders)
    total_units = 0
    total_revenue = 0.0
    product_counter = Counter()

    # Optional week range inference (best-effort)
    week_start = None
    week_end = None
    dates = []

    for o in orders:
        if not isinstance(o, dict):
            continue

        product = o.get("product") or o.get("item") or o.get("name")
        qty = o.get("quantity", 0)

        try:
            qty = int(qty)
        except Exception:
            qty = 0

        if product:
            product_counter[product] += max(qty, 0)

        total_units += max(qty, 0)

        # revenue inference
        if o.get("total_amount") is not None:
            try:
                total_revenue += float(o["total_amount"])
            except Exception:
                pass
        else:
            # try price * quantity
            if o.get("price") is not None:
                try:
                    total_revenue += float(o["price"]) * max(qty, 0)
                except Exception:
                    pass

        # date inference
        if o.get("date"):
            dates.append(o.get("date"))

    if dates:
        # strings like YYYY-MM-DD sort lexicographically correctly
        dates_sorted = sorted(dates)
        week_start = dates_sorted[0]
        week_end = dates_sorted[-1]

    avg_order_value = (
        total_revenue / total_orders) if total_orders > 0 else 0.0
    top_product = product_counter.most_common(
        1)[0][0] if product_counter else None

    # Simple stable scoring: log(1 + revenue)
    sales_score = float(math.log1p(max(total_revenue, 0.0)))

    return {
        "week_start": week_start,
        "week_end": week_end,
        "total_orders": total_orders,
        "total_units": total_units,
        "total_revenue": round(total_revenue, 2),
        "avg_order_value": round(avg_order_value, 2),
        "top_product": top_product,
        "sales_score": round(sales_score, 6),
    }


def store_sales_log(source_endpoint: str, payload: dict, metrics: dict):
    conn = get_db(SALES_LOG_DB_PATH)
    c = conn.cursor()

    c.execute("""
        INSERT INTO weekly_sales_log (
            scored_at,
            week_start, week_end,
            total_orders, total_units, total_revenue, avg_order_value,
            top_product, sales_score,
            source_endpoint, payload
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.utcnow().isoformat(),
        metrics.get("week_start"),
        metrics.get("week_end"),
        metrics["total_orders"],
        metrics["total_units"],
        metrics["total_revenue"],
        metrics["avg_order_value"],
        metrics.get("top_product"),
        metrics["sales_score"],
        source_endpoint,
        json.dumps(payload or {}, ensure_ascii=False)
    ))

    conn.commit()
    log_id = c.lastrowid
    conn.close()
    return log_id

# =========================
# UI (HTML)
# =========================


UI_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Finance Dashboard</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 24px; max-width: 1400px; }
    .row { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
    button { padding: 8px 12px; cursor: pointer; }
    table { border-collapse: collapse; width: 100%; margin-top: 10px; }
    th, td { border: 1px solid #ddd; padding: 8px; vertical-align: top; font-size: 13px; }
    th { background: #f5f5f5; text-align: left; }
    pre { margin: 0; white-space: pre-wrap; word-break: break-word; }
    .card { border: 1px solid #eee; border-radius: 10px; padding: 16px; margin-top: 18px; }
    .muted { color: #666; }
    .pill { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 12px; border: 1px solid #ddd; }
  </style>
</head>
<body>
  <h2>Finance Dashboard</h2>
  <p class="muted">
    Tables:
    <b>purchase_requests</b> (finance DB),
    <b>request_purchase_log</b> (request log DB),
    <b>weekly_sales_log</b> (sales log DB).
  </p>

  <div class="row">
    <button id="refreshAll">Refresh All</button>
    <button id="runSalesScore">Run Weekly Sales Scoring</button>
    <span class="muted" id="statusText">Ready.</span>
  </div>

  <div class="card">
    <div class="row">
      <h3 style="margin:0;">Finance History</h3>
      <span class="pill">GET /finance/history</span>
      <button id="refreshHistory">Refresh</button>
    </div>
    <div style="overflow:auto;">
      <table id="historyTable">
        <thead>
          <tr>
            <th>id</th><th>order_id</th><th>item_name</th><th>qty</th>
            <th>cost</th><th>status</th><th>note</th><th>request_date</th><th>decision_date</th>
          </tr>
        </thead>
        <tbody></tbody>
      </table>
    </div>
  </div>

  <div class="card">
    <div class="row">
      <h3 style="margin:0;">Request Logs</h3>
      <span class="pill">GET /finance/request-log</span>
      <button id="refreshLogs">Refresh</button>
    </div>
    <div style="overflow:auto;">
      <table id="logsTable">
        <thead>
          <tr>
            <th>id</th><th>received_at</th><th>order_id</th><th>item_name</th>
            <th>qty</th><th>unit</th><th>current_stock</th><th>estimated_cost</th>
            <th>status</th><th>http_status</th><th>decision_note</th><th>payload</th>
          </tr>
        </thead>
        <tbody></tbody>
      </table>
    </div>
  </div>

  <div class="card">
    <div class="row">
      <h3 style="margin:0;">Sales Logs</h3>
      <span class="pill">GET /sales/logs</span>
      <button id="refreshSales">Refresh</button>
    </div>
    <div style="overflow:auto;">
      <table id="salesTable">
        <thead>
          <tr>
            <th>id</th><th>scored_at</th><th>week_start</th><th>week_end</th>
            <th>total_orders</th><th>total_units</th><th>total_revenue</th>
            <th>avg_order_value</th><th>top_product</th><th>sales_score</th>
            <th>source_endpoint</th><th>payload</th>
          </tr>
        </thead>
        <tbody></tbody>
      </table>
    </div>
  </div>

<script>
  const statusText = document.getElementById('statusText');

  function escapeHtml(s) {
    if (s === null || s === undefined) return '';
    return String(s)
      .replaceAll('&','&amp;')
      .replaceAll('<','&lt;')
      .replaceAll('>','&gt;')
      .replaceAll('"','&quot;')
      .replaceAll("'","&#039;");
  }

  async function loadHistory() {
    const tbody = document.querySelector('#historyTable tbody');
    tbody.innerHTML = '';
    const res = await fetch('/finance/history');
    const data = await res.json();

    if (!Array.isArray(data)) {
      tbody.innerHTML = `<tr><td colspan="9"><pre>${escapeHtml(JSON.stringify(data, null, 2))}</pre></td></tr>`;
      return;
    }

    for (const row of data) {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td>${escapeHtml(row.id)}</td>
        <td>${escapeHtml(row.order_id)}</td>
        <td>${escapeHtml(row.item_name)}</td>
        <td>${escapeHtml(row.quantity_needed)}</td>
        <td>${escapeHtml(row.estimated_cost)}</td>
        <td>${escapeHtml(row.status)}</td>
        <td>${escapeHtml(row.decision_note)}</td>
        <td>${escapeHtml(row.request_date)}</td>
        <td>${escapeHtml(row.decision_date)}</td>
      `;
      tbody.appendChild(tr);
    }
  }

  async function loadLogs() {
    const tbody = document.querySelector('#logsTable tbody');
    tbody.innerHTML = '';
    const res = await fetch('/finance/request-log');
    const data = await res.json();

    if (!Array.isArray(data)) {
      tbody.innerHTML = `<tr><td colspan="12"><pre>${escapeHtml(JSON.stringify(data, null, 2))}</pre></td></tr>`;
      return;
    }

    for (const row of data) {
      let prettyPayload = row.payload;
      try { prettyPayload = JSON.stringify(JSON.parse(row.payload), null, 2); } catch (e) {}

      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td>${escapeHtml(row.id)}</td>
        <td>${escapeHtml(row.received_at)}</td>
        <td>${escapeHtml(row.order_id)}</td>
        <td>${escapeHtml(row.item_name)}</td>
        <td>${escapeHtml(row.quantity_needed)}</td>
        <td>${escapeHtml(row.unit)}</td>
        <td>${escapeHtml(row.current_stock)}</td>
        <td>${escapeHtml(row.estimated_cost)}</td>
        <td>${escapeHtml(row.status)}</td>
        <td>${escapeHtml(row.http_status)}</td>
        <td>${escapeHtml(row.decision_note)}</td>
        <td><pre>${escapeHtml(prettyPayload)}</pre></td>
      `;
      tbody.appendChild(tr);
    }
  }

  async function loadSales() {
    const tbody = document.querySelector('#salesTable tbody');
    tbody.innerHTML = '';
    const res = await fetch('/sales/logs');
    const data = await res.json();

    if (!Array.isArray(data)) {
      tbody.innerHTML = `<tr><td colspan="12"><pre>${escapeHtml(JSON.stringify(data, null, 2))}</pre></td></tr>`;
      return;
    }

    for (const row of data) {
      let prettyPayload = row.payload;
      try { prettyPayload = JSON.stringify(JSON.parse(row.payload), null, 2); } catch (e) {}

      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td>${escapeHtml(row.id)}</td>
        <td>${escapeHtml(row.scored_at)}</td>
        <td>${escapeHtml(row.week_start)}</td>
        <td>${escapeHtml(row.week_end)}</td>
        <td>${escapeHtml(row.total_orders)}</td>
        <td>${escapeHtml(row.total_units)}</td>
        <td>${escapeHtml(row.total_revenue)}</td>
        <td>${escapeHtml(row.avg_order_value)}</td>
        <td>${escapeHtml(row.top_product)}</td>
        <td>${escapeHtml(row.sales_score)}</td>
        <td>${escapeHtml(row.source_endpoint)}</td>
        <td><pre>${escapeHtml(prettyPayload)}</pre></td>
      `;
      tbody.appendChild(tr);
    }
  }

  async function refreshAll() {
    statusText.textContent = 'Refreshing...';
    try {
      await Promise.all([loadHistory(), loadLogs(), loadSales()]);
      statusText.textContent = 'Updated.';
    } catch (err) {
      statusText.textContent = 'Error: ' + err;
    }
  }

  document.getElementById('refreshHistory').addEventListener('click', async () => {
    statusText.textContent = 'Refreshing history...';
    try { await loadHistory(); statusText.textContent = 'History updated.'; }
    catch (err) { statusText.textContent = 'Error: ' + err; }
  });

  document.getElementById('refreshLogs').addEventListener('click', async () => {
    statusText.textContent = 'Refreshing logs...';
    try { await loadLogs(); statusText.textContent = 'Logs updated.'; }
    catch (err) { statusText.textContent = 'Error: ' + err; }
  });

  document.getElementById('refreshSales').addEventListener('click', async () => {
    statusText.textContent = 'Refreshing sales...';
    try { await loadSales(); statusText.textContent = 'Sales updated.'; }
    catch (err) { statusText.textContent = 'Error: ' + err; }
  });

  document.getElementById('refreshAll').addEventListener('click', refreshAll);

  document.getElementById('runSalesScore').addEventListener('click', async () => {
    statusText.textContent = 'Running sales scoring...';
    try {
      const res = await fetch('/sales/score-weekly', { method: 'POST' });
      const data = await res.json();
      statusText.textContent = 'Sales scoring done. Log ID: ' + (data.log_id ?? 'n/a');
      await loadSales();
    } catch (err) {
      statusText.textContent = 'Error: ' + err;
    }
  });

  // Auto-load on page open
  refreshAll();
</script>
</body>
</html>
"""


@app.route("/ui", methods=["GET"])
def ui():
    return render_template_string(UI_HTML)

# =========================
# API ROUTES (FINANCE)
# =========================


@app.route("/", methods=["GET"])
def health():
    return jsonify({"message": "Finance subsystem running"})


@app.route("/PurchaseRequest", methods=["POST"])
def process_purchase_request():
    data = request.get_json(silent=True)

    required_fields = ["order_id", "item_name",
                       "quantity_needed", "estimated_cost"]
    if not data or not all(field in data for field in required_fields):
        log_request_purchase(
            data=data,
            status="INVALID",
            note="Incomplete purchase request data",
            http_status=400
        )
        return jsonify({"error": "Incomplete purchase request data"}), 400

    status, note = evaluate_purchase_request(data["estimated_cost"])
    now = datetime.utcnow().isoformat()

    # Write to finance DB
    conn = get_db(FINANCE_DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO purchase_requests (
            order_id, item_name, quantity_needed, unit, current_stock,
            estimated_cost, status, decision_note, request_date, decision_date
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

    http_status = 201 if status == "APPROVED" else 403

    # Log to request log DB
    log_request_purchase(
        data=data,
        status=status,
        note=note,
        http_status=http_status
    )

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
    }), http_status


@app.route("/finance/history", methods=["GET"])
def finance_history():
    conn = get_db(FINANCE_DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT
            id, order_id, item_name, quantity_needed, estimated_cost,
            status, decision_note, request_date, decision_date
        FROM purchase_requests
        ORDER BY request_date DESC
    """)
    rows = c.fetchall()
    conn.close()
    return jsonify([dict(row) for row in rows])


@app.route("/finance/request-log", methods=["GET"])
def request_log():
    conn = get_db(REQUEST_LOG_DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT
            id, received_at, order_id, item_name, quantity_needed, unit,
            current_stock, estimated_cost, status, decision_note, http_status, payload
        FROM request_purchase_log
        ORDER BY received_at DESC
    """)
    rows = c.fetchall()
    conn.close()
    return jsonify([dict(row) for row in rows])

# =========================
# API ROUTES (SALES)
# =========================


@app.route("/sales/score-weekly", methods=["POST"])
def sales_score_weekly():
    """
    Fetch weekly order data from order_app, compute metrics + score, store in indago_sales_log.db
    """
    try:
        source_endpoint, payload = fetch_weekly_orders()
    except Exception as exc:
        return jsonify({
            "status": "error",
            "message": "Failed to fetch weekly orders from order_app",
            "details": str(exc)
        }), 502

    orders = normalize_orders_payload(payload)
    # If payload itself is a list, support that too:
    if not orders and isinstance(payload, list):
        orders = payload

    metrics = compute_sales_metrics(orders)
    log_id = store_sales_log(source_endpoint, payload, metrics)

    return jsonify({
        "status": "success",
        "log_id": log_id,
        "metrics": metrics,
        "source_endpoint": source_endpoint
    }), 201


@app.route("/sales/logs", methods=["GET"])
def sales_logs():
    conn = get_db(SALES_LOG_DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT
            id, scored_at, week_start, week_end,
            total_orders, total_units, total_revenue, avg_order_value,
            top_product, sales_score, source_endpoint, payload
        FROM weekly_sales_log
        ORDER BY scored_at DESC
    """)
    rows = c.fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

# =========================
# STARTUP
# =========================


if __name__ == "__main__":
    init_finance_db()
    init_request_log_db()
    init_sales_log_db()
    app.run(port=5003, debug=True)

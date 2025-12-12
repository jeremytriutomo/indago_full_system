from datetime import datetime
import json
import sqlite3
import requests

from flask import Flask, jsonify, request, render_template_string
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


def log_procurement(cursor, payload, status, response_body):
    cursor.execute("""
        INSERT INTO procurement_log
        (order_id, item_name, quantity_needed, unit, status, payload, response, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        payload.get("order_id"),
        payload.get("item_name"),
        payload.get("quantity_needed"),
        payload.get("unit"),
        status,
        json.dumps(payload, ensure_ascii=False),
        response_body,
        datetime.utcnow().isoformat()
    ))


def trigger_purchase_request(cursor, item, remaining):
    """
    Auto-triggered procurement when stock is low.
    """
    if has_open_procurement(cursor, item):
        return

    payload = {
        "order_id": f"PR-{item}-{int(datetime.utcnow().timestamp())}",
        "item_name": item,
        "quantity_needed": calculate_replenishment_quantity(item, remaining),
        "unit": ITEM_UNITS.get(item),
        "current_stock": remaining,
        "estimated_cost": 0  # placeholder to satisfy finance required fields
    }

    status = "pending"
    response_body = None

    try:
        resp = requests.post(FINANCE_PURCHASE_ENDPOINT,
                             json=payload, timeout=5)
        response_body = resp.text
        resp.raise_for_status()
        status = "submitted"
    except Exception as e:
        status = "failed"
        response_body = str(e)

    log_procurement(cursor, payload, status, response_body)

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
        fetched = c.fetchone()
        if fetched is None:
            c.execute(
                "INSERT OR IGNORE INTO inventory (item, quantity) VALUES (?, ?)", (item, 0))
            current = 0
        else:
            current = fetched["quantity"]

        new_qty = max(current - qty, 0)
        c.execute("UPDATE inventory SET quantity = ? WHERE item = ?",
                  (new_qty, item))

        if should_trigger_purchase(item, new_qty):
            trigger_purchase_request(c, item, new_qty)

    conn.commit()
    conn.close()

# =========================
# UI
# =========================


UI_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Inventory UI</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 24px; max-width: 1100px; }
    .row { display:flex; gap:12px; align-items:center; flex-wrap:wrap; }
    input, select, button { padding: 8px 10px; }
    button { cursor:pointer; }
    .card { border:1px solid #eee; border-radius:10px; padding:16px; margin-top:16px; }
    .muted { color:#666; }
    table { border-collapse: collapse; width:100%; margin-top:10px; }
    th, td { border:1px solid #ddd; padding:8px; font-size:13px; }
    th { background:#f5f5f5; text-align:left; }
    pre { margin:0; background:#f7f7f7; padding:10px; border-radius:8px; overflow:auto; }
    .grid { display:grid; grid-template-columns: 160px 1fr; gap:10px; max-width: 700px; }
  </style>
</head>
<body>
  <h2>Inventory Subsystem</h2>
  <p class="muted">
    This UI can:
    <b>(1)</b> trigger consumption from Kitchen (<code>POST /consume</code>) and show material usage,
    <b>(2)</b> manually send a purchase request to Finance (<code>POST /purchase-request</code> → Finance <code>/PurchaseRequest</code>).
  </p>

  <div class="row">
    <label for="date">Production date:</label>
    <input id="date" type="date" />
    <button id="consumeBtn">Consume from Kitchen</button>
    <button id="refreshStockBtn">Refresh Stock</button>
    <span class="muted" id="status">Ready.</span>
  </div>

  <div class="card">
    <h3 style="margin:0;">Material Usage (items_consumed)</h3>
    <p class="muted">Result from <code>/consume</code></p>
    <pre id="usageOut">No data yet.</pre>
  </div>

  <div class="card">
    <h3 style="margin:0;">Manual Purchase Request</h3>
    <p class="muted">Sends approval request to Finance: <code>POST /purchase-request</code></p>

    <div class="grid" style="margin-top:10px;">
      <div>Item</div>
      <div>
        <select id="prItem">
          <option value="beans">beans</option>
          <option value="milk">milk</option>
        </select>
      </div>

      <div>Quantity Needed</div>
      <div><input id="prQty" type="number" min="1" value="1000" /></div>

      <div>Estimated Cost</div>
      <div><input id="prCost" type="number" min="0" value="100000" /></div>

      <div>Order ID (optional)</div>
      <div><input id="prOrderId" type="text" placeholder="Leave blank to auto-generate" /></div>
    </div>

    <div class="row" style="margin-top:12px;">
      <button id="sendPRBtn">Send Purchase Request</button>
    </div>

    <h4>Finance Response</h4>
    <pre id="prOut">No purchase request sent yet.</pre>
  </div>

  <div class="card">
    <h3 style="margin:0;">Current Stock</h3>
    <p class="muted">Result from <code>GET /stock</code></p>
    <div style="overflow:auto;">
      <table>
        <thead>
          <tr><th>Item</th><th>Quantity</th><th>Unit</th></tr>
        </thead>
        <tbody id="stockBody"></tbody>
      </table>
    </div>
  </div>

<script>
  const dateInput = document.getElementById('date');
  const statusEl = document.getElementById('status');
  const usageOut = document.getElementById('usageOut');
  const stockBody = document.getElementById('stockBody');
  const prOut = document.getElementById('prOut');

  // default date = today
  dateInput.valueAsDate = new Date();

  function escapeHtml(s) {
    if (s === null || s === undefined) return '';
    return String(s)
      .replaceAll('&','&amp;')
      .replaceAll('<','&lt;')
      .replaceAll('>','&gt;')
      .replaceAll('"','&quot;')
      .replaceAll("'","&#039;");
  }

  async function loadStock() {
    stockBody.innerHTML = '';
    const res = await fetch('/stock');
    const data = await res.json();
    const stock = data.stock || [];
    for (const row of stock) {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td>${escapeHtml(row.item)}</td>
        <td>${escapeHtml(row.quantity)}</td>
        <td>${escapeHtml(row.unit)}</td>
      `;
      stockBody.appendChild(tr);
    }
  }

  document.getElementById('refreshStockBtn').addEventListener('click', async () => {
    statusEl.textContent = 'Refreshing stock...';
    try {
      await loadStock();
      statusEl.textContent = 'Stock updated.';
    } catch (err) {
      statusEl.textContent = 'Error: ' + err;
    }
  });

  document.getElementById('consumeBtn').addEventListener('click', async () => {
    const date = dateInput.value;
    if (!date) {
      alert('Pick a date first.');
      return;
    }

    statusEl.textContent = 'Consuming...';
    usageOut.textContent = 'Running...';

    try {
      const res = await fetch(`/consume?date=${encodeURIComponent(date)}`, {
        method: 'POST'
      });

      const data = await res.json();
      usageOut.textContent = JSON.stringify(data, null, 2);

      await loadStock();
      statusEl.textContent = 'Done.';
    } catch (err) {
      statusEl.textContent = 'Error: ' + err;
      usageOut.textContent = 'Error: ' + err;
    }
  });

  document.getElementById('sendPRBtn').addEventListener('click', async () => {
    prOut.textContent = 'Sending...';
    statusEl.textContent = 'Sending purchase request...';

    const item = document.getElementById('prItem').value;
    const qty = Number(document.getElementById('prQty').value || 0);
    const cost = Number(document.getElementById('prCost').value || 0);
    const orderIdRaw = document.getElementById('prOrderId').value.trim();

    if (!item || qty <= 0) {
      prOut.textContent = 'Invalid input: item and positive quantity are required.';
      statusEl.textContent = 'Input error.';
      return;
    }

    const payload = {
      item_name: item,
      quantity_needed: qty,
      estimated_cost: cost
    };

    if (orderIdRaw) payload.order_id = orderIdRaw;

    try {
      const res = await fetch('/purchase-request', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
      });

      const data = await res.json();
      prOut.textContent = JSON.stringify(data, null, 2);

      // refresh stock just in case you want the UI always current
      await loadStock();

      statusEl.textContent = 'Purchase request sent.';
    } catch (err) {
      prOut.textContent = 'Error: ' + err;
      statusEl.textContent = 'Error: ' + err;
    }
  });

  // initial load
  loadStock();
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


@app.route("/purchase-request", methods=["POST"])
def manual_purchase_request():
    """
    Manual trigger: send a PurchaseRequest to finance app, and log it in procurement_log.
    Body:
      {
        "item_name": "beans",
        "quantity_needed": 1000,
        "estimated_cost": 100000,
        "order_id": "optional"
      }
    """
    data = request.get_json(silent=True) or {}

    required = ["item_name", "quantity_needed", "estimated_cost"]
    if not all(k in data for k in required):
        return jsonify({"error": f"Missing required fields: {required}"}), 400

    item = data["item_name"]
    try:
        qty_needed = int(data["quantity_needed"])
    except Exception:
        return jsonify({"error": "quantity_needed must be an integer"}), 400

    try:
        est_cost = float(data["estimated_cost"])
    except Exception:
        return jsonify({"error": "estimated_cost must be a number"}), 400

    # Read current stock for info
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT quantity FROM inventory WHERE item = ?", (item,))
    row = c.fetchone()
    current_stock = int(row["quantity"]) if row else 0

    order_id = data.get(
        "order_id") or f"MANUAL-PR-{item}-{int(datetime.utcnow().timestamp())}"

    payload = {
        "order_id": order_id,
        "item_name": item,
        "quantity_needed": qty_needed,
        "unit": ITEM_UNITS.get(item, "units"),
        "current_stock": current_stock,
        "estimated_cost": est_cost
    }

    status = "pending"
    response_body = None
    http_status = None

    try:
        resp = requests.post(FINANCE_PURCHASE_ENDPOINT,
                             json=payload, timeout=5)
        response_body = resp.text
        http_status = resp.status_code
        resp.raise_for_status()
        status = "submitted"
    except Exception as e:
        status = "failed"
        response_body = str(e)

    # Log to procurement_log
    log_procurement(c, payload, status, response_body)
    conn.commit()
    conn.close()

    # Return a helpful response to UI
    return jsonify({
        "message": "Purchase request sent to finance",
        "inventory_log_status": status,
        "finance_http_status": http_status,
        "payload_sent": payload,
        "finance_response": response_body
    }), 200 if status != "failed" else 502


@app.route("/", methods=["GET"])
def health():
    return jsonify({"message": "Inventory subsystem running"})

# =========================
# STARTUP
# =========================


if __name__ == "__main__":
    init_db()
    app.run(port=5002, debug=True)

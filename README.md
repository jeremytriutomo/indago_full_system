☕ inDago – Integrated Coffee Ordering System

System Integration Final Project
Sampoerna University – Faculty of Engineering and Technology

📌 Overview

inDago is a multi-service, REST-based system that simulates a real-world coffee business operation.
It integrates Order, Kitchen, Inventory, and Finance subsystems using loosely coupled APIs, each owning its own database and business logic.

The project demonstrates:

Service-oriented architecture (SOA)

End-to-end workflow orchestration

Persistent audit logging

Manual & automated operational control

Sales analytics and scoring

🧱 System Architecture
order_app ──▶ kitchen_app ──▶ inventory_app ──▶ finance_app
     ▲                                          │
     └────────────── sales analytics ◀──────────┘


All communication via HTTP (REST)

No shared databases

Independent deployment per service

🚀 Subsystems & Ports
Subsystem	Description	Port
order_app	Order management & weekly aggregation	5001
kitchen_app	Production planning & batch calculation	5004
inventory_app	Stock tracking & procurement trigger	5002
finance_app	Purchase approval & sales analytics	5003
🔌 API Summary
Order Service (order_app)

POST /add-order – Create customer order

POST /aggregate – Aggregate weekly orders

GET /orders-weekly – Retrieve weekly orders

GET /weekly-order – Preferred endpoint for analytics

Kitchen Service (kitchen_app)

POST /start-production – Trigger production for a date

GET /batch?date=YYYY-MM-DD – Retrieve ingredient usage

Inventory Service (inventory_app)

GET /stock – View current inventory

POST /consume?date=YYYY-MM-DD – Apply kitchen consumption

POST /purchase-request – Manual procurement request

Finance Service (finance_app)

POST /PurchaseRequest – Approve / reject procurement

GET /finance/history – Procurement decisions

GET /finance/request-log – Raw request logs

POST /sales/score-weekly – Weekly sales scoring

GET /sales/logs – Sales analytics history

🖥️ User Interfaces

Each operational subsystem includes a lightweight HTML UI.

Subsystem	URL	Purpose
Kitchen	/ui	Trigger production & view batch
Inventory	/ui	Consume stock & send purchase requests
Finance	/ui	View finance logs & sales analytics

Example:

http://127.0.0.1:5002/ui   # Inventory UI
http://127.0.0.1:5003/ui   # Finance UI

🗄️ Databases

Each service owns its own SQLite database:

Service	Database
order_app	indago_orders.db
kitchen_app	indago_kitchen.db
inventory_app	indago_inventory.db
finance_app	indago_financial_records.db
finance_app (logs)	indago_request_log.db
finance_app (analytics)	indago_sales_log.db
🔁 End-to-End Workflow

Orders created (order_app)

Weekly aggregation

Production planning (kitchen_app)

Inventory consumption (inventory_app)

Procurement approval (finance_app)

Sales scoring & analytics

Both automatic (low stock) and manual (UI-triggered) workflows are supported.

⚙️ Installation & Run
Prerequisites

Python 3.9+

pip

Install dependencies
pip install flask flask-cors requests

Run services (recommended order)
python order_app.py
python kitchen_app.py
python inventory_app.py
python finance_app.py

🧠 Design Principles

Loose coupling – REST-only communication

Single responsibility – One domain per service

Auditability – Persistent logs for all critical actions

Extensibility – Easy to add new services or analytics

Real-world mapping – Mirrors SME operations

📚 Academic Context

This project was developed as part of a System Integration course and demonstrates practical implementation of:

Service-oriented architecture

API contract management

Data flow orchestration

Operational and analytical system layers

📜 License

Academic use only.

✨ Author

Jeremy Triutomo Putra
Faculty of Engineering and Technology
Sampoerna University

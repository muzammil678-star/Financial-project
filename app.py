import logging
import os
from datetime import datetime
from decimal import Decimal

from flask import send_from_directory
import numpy as np
from flask import Flask, jsonify, request
from sqlalchemy import text

from db import engine

app = Flask(__name__)
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

access_logger = logging.getLogger("access")
access_logger.setLevel(logging.INFO)
if not access_logger.handlers:
    handler = logging.FileHandler(os.path.join(LOG_DIR, "access.log"))
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    access_logger.addHandler(handler)


@app.after_request
def log_access(response):
    access_logger.info(
        "%s user=%s %s %s -> %s",
        request.remote_addr, current_user(), request.method,
        request.full_path.rstrip("?"), response.status_code,
    )
    return response

REVIEW_STATUSES = ("PENDING", "UNDER_REVIEW", "CLEARED", "ESCALATED")
ALERT_CUTOFF = 35   # score needed to flag a transaction for review
HIGH_CUTOFF = 65    # score from which an alert counts as HIGH

ALERT_SQL = """
    SELECT a.alert_id, a.transaction_id, a.rule_name, a.risk_score,
           a.reason, a.review_status, a.created_at,
           t.account_id, t.transaction_datetime, t.amount, t.region
    FROM alerts a
    JOIN transactions t ON t.transaction_id = a.transaction_id
"""


def clean(row):
    d = dict(row)
    for key, value in d.items():
        if isinstance(value, Decimal):
            d[key] = float(value)
        elif hasattr(value, "isoformat"):
            d[key] = value.isoformat()
    return d


def current_user():
    return (request.headers.get("X-User") or "anonymous")[:50]


def get_limit(default=20, maximum=200):
    limit = request.args.get("limit", default, type=int)
    return max(1, min(limit, maximum))


def audit(conn, action, entity, entity_id, details):
    conn.execute(
        text("INSERT INTO audit_log (user_name, action, entity, entity_id, details) "
             "VALUES (:u, :a, :e, :i, :d)"),
        {"u": current_user(), "a": action, "e": entity,
         "i": str(entity_id), "d": details[:255]},
    )


def fetch_alert(conn, alert_id):
    return conn.execute(
        text(ALERT_SQL + " WHERE a.alert_id = :id"), {"id": alert_id}
    ).mappings().first()

@app.get("/")
def home():
    return jsonify(
        name="Financial Risk Analysis and Rule-Based Fraud Detection API",
        note="Flagged transactions are flagged for review, not confirmed fraud.",
    )

@app.get("/health")
def health():
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return jsonify(status="ok")

@app.get("/alerts")
def list_alerts():
    status = request.args.get("status")
    if status:
        status = status.upper()
        if status not in REVIEW_STATUSES:
            return jsonify(error=f"status must be one of {list(REVIEW_STATUSES)}"), 400

    sql = ALERT_SQL
    params = {"limit": get_limit()}
    if status:
        sql += " WHERE a.review_status = :status"
        params["status"] = status
    sql += " ORDER BY a.risk_score DESC, a.alert_id LIMIT :limit"

    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    return jsonify([clean(r) for r in rows])


@app.get("/alerts/<int:alert_id>")
def get_alert(alert_id):
    with engine.connect() as conn:
        row = fetch_alert(conn, alert_id)
    if row is None:
        return jsonify(error="Alert not found"), 404
    return jsonify(clean(row))

@app.patch("/alerts/<int:alert_id>")
def update_alert(alert_id):
    body = request.get_json(silent=True) or {}
    new_status = str(body.get("review_status", "")).upper()
    if new_status not in REVIEW_STATUSES:
        return jsonify(error=f"review_status must be one of {list(REVIEW_STATUSES)}"), 400
    note = str(body.get("note", ""))[:150]

    with engine.begin() as conn:
        alert = fetch_alert(conn, alert_id)
        if alert is None:
            return jsonify(error="Alert not found"), 404
        old_status = alert["review_status"]
        conn.execute(
            text("UPDATE alerts SET review_status = :s WHERE alert_id = :id"),
            {"s": new_status, "id": alert_id},
        )
        details = f"{old_status} -> {new_status}"
        if note:
            details += f" | {note}"
        audit(conn, "ALERT_STATUS_CHANGE", "alert", alert_id, details)
        updated = fetch_alert(conn, alert_id)
    return jsonify(clean(updated))

@app.get("/transactions")
def list_transactions():
    account_id = request.args.get("account_id", type=int)
    region = request.args.get("region")

    sql = """
        SELECT transaction_id, account_id, transaction_datetime, amount,
               transaction_type, channel, region
        FROM transactions
        WHERE 1 = 1
    """
    params = {"limit": get_limit()}
    if account_id is not None:
        sql += " AND account_id = :account_id"
        params["account_id"] = account_id
    if region:
        sql += " AND region = :region"
        params["region"] = region
    sql += " ORDER BY transaction_datetime DESC, transaction_id DESC LIMIT :limit"

    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    return jsonify([clean(r) for r in rows])


@app.get("/transactions/<int:transaction_id>")
def get_transaction(transaction_id):
    sql = text("""
        SELECT transaction_id, account_id, transaction_datetime, amount,
               transaction_type, channel, region
        FROM transactions
        WHERE transaction_id = :id
    """)
    with engine.connect() as conn:
        row = conn.execute(sql, {"id": transaction_id}).mappings().first()
    if row is None:
        return jsonify(error="Transaction not found"), 404
    return jsonify(clean(row))

def risk_level(score):
    if score >= HIGH_CUTOFF:
        return "HIGH"
    if score >= ALERT_CUTOFF:
        return "MEDIUM"
    return "LOW"


def to_dt(value):
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    return value


def score_transaction(conn, account_id, amount, when, region):
    rule_rows = conn.execute(text(
        "SELECT rule_name, weight, threshold FROM rule_config WHERE is_active = 1"
    )).mappings().all()
    rules = {r["rule_name"]: (int(r["weight"]), float(r["threshold"] or 0))
             for r in rule_rows}

    history = conn.execute(
        text("SELECT amount, transaction_datetime, region "
             "FROM transactions WHERE account_id = :a"),
        {"a": account_id},
    ).mappings().all()
    amounts = np.array([float(h["amount"]) for h in history])
    times = [to_dt(h["transaction_datetime"]) for h in history]
    regions = [h["region"] for h in history]
    n = len(history)

    points, names, reasons = 0, [], []

    if "UNUSUAL_AMOUNT" in rules and n >= 10:
        weight, threshold = rules["UNUSUAL_AMOUNT"]
        std = amounts.std(ddof=1)
        if std > 0:
            z = (amount - amounts.mean()) / std
            if z >= threshold:
                points += weight
                names.append("UNUSUAL_AMOUNT")
                reasons.append(f"Amount {z:.1f} std devs above account average.")

    if "HIGH_FREQUENCY" in rules:
        weight, threshold = rules["HIGH_FREQUENCY"]
        count = 1 + sum(1 for t in times if abs((t - when).total_seconds()) <= 600)
        if count >= threshold:
            points += weight
            names.append("HIGH_FREQUENCY")
            reasons.append(f"{count} transactions within 10 minutes.")

    if "LOCATION_CHANGE" in rules and n >= 10:
        weight, threshold = rules["LOCATION_CHANGE"]
        share = sum(1 for r in regions if r == region) / n
        if share < threshold and when.hour < 5:
            points += weight
            names.append("LOCATION_CHANGE")
            reasons.append(f"Region {region} used in {share * 100:.1f}% of account history, at night.")

    score = min(points, 100)
    return {
        "risk_score": score,
        "risk_level": risk_level(score),
        "flagged": score >= ALERT_CUTOFF,
        "rules_triggered": names,
        "rule_name": "+".join(names),
        "reason": " ".join(reasons),
    }

@app.post("/transactions")
# or: @app.route("/transactions", methods=["POST"])
def import_transaction():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error="Send a JSON object in the request body"), 400

    errors = []
    account_id = body.get("account_id")
    if not isinstance(account_id, int) or isinstance(account_id, bool):
        errors.append("account_id must be an integer")

    amount = body.get("amount")
    if isinstance(amount, bool) or not isinstance(amount, (int, float)) or amount <= 0:
        errors.append("amount must be a number greater than 0")

    region = body.get("region")
    if not isinstance(region, str) or not region.strip() or len(region) > 50:
        errors.append("region must be text of 1 to 50 characters")

    when = datetime.now().replace(microsecond=0)
    if body.get("transaction_datetime"):
        try:
            when = datetime.fromisoformat(str(body["transaction_datetime"])).replace(tzinfo=None)
        except ValueError:
            errors.append("transaction_datetime must look like 2026-10-20T02:40:00")

    txn_type = body.get("transaction_type")
    channel = body.get("channel")
    for name, value in (("transaction_type", txn_type), ("channel", channel)):
        if value is not None and (not isinstance(value, str) or len(value) > 30):
            errors.append(f"{name} must be text of up to 30 characters")

    if errors:
        return jsonify(errors=errors), 400
    amount = round(float(amount), 2)
    region = region.strip()

    with engine.begin() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM accounts WHERE account_id = :a"), {"a": account_id}
        ).first()
        if exists is None:
            return jsonify(error="Account not found"), 404

        verdict = score_transaction(conn, account_id, amount, when, region)

        result = conn.execute(
            text("INSERT INTO transactions (account_id, transaction_datetime, amount, "
                 "transaction_type, channel, region, is_planted_anomaly) "
                 "VALUES (:a, :dt, :amt, :tt, :ch, :rg, 0)"),
            {"a": account_id, "dt": when.strftime("%Y-%m-%d %H:%M:%S"),
             "amt": amount, "tt": txn_type, "ch": channel, "rg": region},
        )
        txn_id = result.lastrowid
        audit(conn, "TRANSACTION_IMPORT", "transaction", txn_id,
              f"account {account_id}, amount {amount}, region {region}")

        alert_id = None
        if verdict["flagged"]:
            alert_result = conn.execute(
                text("INSERT INTO alerts (transaction_id, rule_name, risk_score, reason) "
                     "VALUES (:t, :r, :s, :why)"),
                {"t": txn_id, "r": verdict["rule_name"][:50],
                 "s": verdict["risk_score"], "why": verdict["reason"][:255]},
            )
            alert_id = alert_result.lastrowid
            audit(conn, "ALERT_CREATED", "alert", alert_id,
                  f"score {verdict['risk_score']}: {verdict['rule_name']}")

    return jsonify(
        transaction_id=txn_id,
        alert_id=alert_id,
        risk_score=verdict["risk_score"],
        risk_level=verdict["risk_level"],
        flagged_for_review=verdict["flagged"],
        rules_triggered=verdict["rules_triggered"],
        reason=verdict["reason"],
        review_status="PENDING" if verdict["flagged"] else None,
    ), 201

@app.get("/rules")
# or: @app.route("/rules")
def list_rules():
    sql = text("""
        SELECT rule_name, weight, threshold, description, is_active
        FROM rule_config
        ORDER BY weight DESC, rule_name
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql).mappings().all()
    return jsonify([clean(r) for r in rows])

@app.get("/reports/risk-distribution")
# or: @app.route("/reports/risk-distribution")
def risk_distribution():
    with engine.connect() as conn:
        by_level = conn.execute(text("""
            SELECT CASE WHEN risk_score >= :high THEN 'HIGH' ELSE 'MEDIUM' END AS risk_level,
                   COUNT(*) AS alerts
            FROM alerts
            GROUP BY risk_level
            ORDER BY alerts DESC
        """), {"high": HIGH_CUTOFF}).mappings().all()
        by_status = conn.execute(text("""
            SELECT review_status, COUNT(*) AS alerts
            FROM alerts
            GROUP BY review_status
            ORDER BY alerts DESC
        """)).mappings().all()
    return jsonify(
        by_level=[clean(r) for r in by_level],
        by_status=[clean(r) for r in by_status],
    )

@app.get("/reports/alert-trends")
# or: @app.route("/reports/alert-trends")
def alert_trends():
    sql = text("""
        SELECT DATE(t.transaction_datetime) AS alert_date, COUNT(*) AS alerts
        FROM alerts a
        JOIN transactions t ON t.transaction_id = a.transaction_id
        GROUP BY alert_date
        ORDER BY alert_date
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql).mappings().all()
    return jsonify([clean(r) for r in rows])


@app.get("/audit-log")
# or: @app.route("/audit-log")
def list_audit_log():
    sql = text("""
        SELECT log_id, user_name, action, entity, entity_id, details, created_at
        FROM audit_log
        ORDER BY created_at DESC, log_id DESC
        LIMIT :limit
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"limit": get_limit(50, 500)}).mappings().all()
    return jsonify([clean(r) for r in rows])

@app.get("/charts/risk-distribution")
def risk_distribution_chart():
    return send_from_directory(
        "reports", "risk_distribution.png"
    )


@app.get("/charts/alert-trend")
def alert_trend_chart():
    return send_from_directory(
        "reports", "alert_trend.png"
    )


@app.get("/charts/alerts-by-rule")
def alerts_by_rule_chart():
    return send_from_directory(
        "reports", "alerts_by_rule.png"
    )

if __name__ == "__main__":
    app.run(debug=True)

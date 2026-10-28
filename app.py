import csv
import io
import logging
import os
from datetime import datetime
from decimal import Decimal
from flask import Flask, jsonify, request, render_template,Response
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

# ---------- 1. GET /accounts ----------
@app.get("/accounts")
# or: @app.route("/accounts")
def list_accounts():
    sql = text("""
        SELECT a.account_id, a.customer_id, c.customer_code
        FROM accounts a
        JOIN customers c ON c.customer_id = a.customer_id
        ORDER BY a.account_id
        LIMIT :limit
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"limit": get_limit(50, 500)}).mappings().all()
    return jsonify([clean(r) for r in rows])


# ---------- 2. GET /accounts/<id> ----------
@app.get("/accounts/<int:account_id>")
# or: @app.route("/accounts/<int:account_id>")
def get_account(account_id):
    with engine.connect() as conn:
        account = conn.execute(text("""
            SELECT a.account_id, a.customer_id, c.customer_code
            FROM accounts a
            JOIN customers c ON c.customer_id = a.customer_id
            WHERE a.account_id = :id
        """), {"id": account_id}).mappings().first()
        if account is None:
            return jsonify(error="Account not found"), 404

        stats = conn.execute(text("""
            SELECT COUNT(*) AS transactions, AVG(amount) AS avg_amount,
                   MAX(amount) AS max_amount,
                   MIN(transaction_datetime) AS first_transaction,
                   MAX(transaction_datetime) AS last_transaction
            FROM transactions
            WHERE account_id = :id
        """), {"id": account_id}).mappings().first()

        regions = conn.execute(text("""
            SELECT region, COUNT(*) AS transactions
            FROM transactions
            WHERE account_id = :id
            GROUP BY region
            ORDER BY transactions DESC
        """), {"id": account_id}).mappings().all()

        alerts = conn.execute(text("""
            SELECT a.review_status, COUNT(*) AS alerts
            FROM alerts a
            JOIN transactions t ON t.transaction_id = a.transaction_id
            WHERE t.account_id = :id
            GROUP BY a.review_status
        """), {"id": account_id}).mappings().all()

    stats = clean(stats)
    if stats["avg_amount"] is not None:
        stats["avg_amount"] = round(stats["avg_amount"], 2)
    return jsonify(
        account=clean(account),
        stats=stats,
        regions=[clean(r) for r in regions],
        alerts_by_status=[clean(r) for r in alerts],
    )


# ---------- 3. GET /alerts/<id>/history ----------
@app.get("/alerts/<int:alert_id>/history")
# or: @app.route("/alerts/<int:alert_id>/history")
def alert_history(alert_id):
    with engine.connect() as conn:
        if fetch_alert(conn, alert_id) is None:
            return jsonify(error="Alert not found"), 404
        rows = conn.execute(text("""
            SELECT log_id, user_name, action, details, created_at
            FROM audit_log
            WHERE entity = 'alert' AND entity_id = :id
            ORDER BY created_at, log_id
        """), {"id": str(alert_id)}).mappings().all()
    return jsonify([clean(r) for r in rows])


# ---------- 4. PATCH /rules/<name> ----------
@app.patch("/rules/<rule_name>")
# or: @app.route("/rules/<rule_name>", methods=["PATCH"])
def update_rule(rule_name):
    body = request.get_json(silent=True) or {}
    changes, errors = [], []
    params = {"name": rule_name}

    if "weight" in body:
        w = body["weight"]
        if isinstance(w, bool) or not isinstance(w, int) or not 0 <= w <= 100:
            errors.append("weight must be a whole number from 0 to 100")
        else:
            changes.append("weight = :weight")
            params["weight"] = w
    if "threshold" in body:
        th = body["threshold"]
        if isinstance(th, bool) or not isinstance(th, (int, float)) or th < 0:
            errors.append("threshold must be a number of 0 or more")
        else:
            changes.append("threshold = :threshold")
            params["threshold"] = th
    if "is_active" in body:
        if not isinstance(body["is_active"], bool):
            errors.append("is_active must be true or false")
        else:
            changes.append("is_active = :active")
            params["active"] = 1 if body["is_active"] else 0

    if errors:
        return jsonify(errors=errors), 400
    if not changes:
        return jsonify(error="Send weight, threshold or is_active"), 400

    select_sql = text("SELECT rule_name, weight, threshold, description, is_active "
                      "FROM rule_config WHERE rule_name = :name")
    with engine.begin() as conn:
        before = conn.execute(select_sql, {"name": rule_name}).mappings().first()
        if before is None:
            return jsonify(error="Rule not found"), 404
        conn.execute(text("UPDATE rule_config SET " + ", ".join(changes)
                          + " WHERE rule_name = :name"), params)
        after = conn.execute(select_sql, {"name": rule_name}).mappings().first()
        audit(conn, "RULE_UPDATE", "rule", rule_name,
              f"weight {before['weight']}->{after['weight']}, "
              f"threshold {float(before['threshold'] or 0)}->{float(after['threshold'] or 0)}, "
              f"active {before['is_active']}->{after['is_active']}")
    return jsonify(clean(after))


# ---------- 5. GET /reports/alerts-by-rule ----------
@app.get("/reports/alerts-by-rule")
# or: @app.route("/reports/alerts-by-rule")
def alerts_by_rule():
    sql = text("""
        SELECT rule_name, COUNT(*) AS alerts, AVG(risk_score) AS avg_score
        FROM alerts
        GROUP BY rule_name
        ORDER BY alerts DESC
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql).mappings().all()
    result = [clean(r) for r in rows]
    for r in result:
        r["avg_score"] = round(r["avg_score"], 1)
    return jsonify(result)


# ---------- 6. GET /reports/evaluation ----------
@app.get("/reports/evaluation")
# or: @app.route("/reports/evaluation")
def evaluation():
    sql = text("""
        SELECT
          SUM(CASE WHEN t.is_planted_anomaly = 1 AND f.transaction_id IS NOT NULL THEN 1 ELSE 0 END) AS true_positives,
          SUM(CASE WHEN t.is_planted_anomaly = 0 AND f.transaction_id IS NOT NULL THEN 1 ELSE 0 END) AS false_positives,
          SUM(CASE WHEN t.is_planted_anomaly = 1 AND f.transaction_id IS NULL THEN 1 ELSE 0 END) AS false_negatives,
          SUM(CASE WHEN t.is_planted_anomaly = 0 AND f.transaction_id IS NULL THEN 1 ELSE 0 END) AS true_negatives
        FROM transactions t
        LEFT JOIN (SELECT DISTINCT transaction_id FROM alerts) f
               ON f.transaction_id = t.transaction_id
    """)
    with engine.connect() as conn:
        row = conn.execute(sql).mappings().first()
    tp, fp = int(row["true_positives"] or 0), int(row["false_positives"] or 0)
    fn, tn = int(row["false_negatives"] or 0), int(row["true_negatives"] or 0)
    precision = tp / max(tp + fp, 1)
    recall = tp / max(tp + fn, 1)
    f1 = 2 * precision * recall / max(precision + recall, 1e-9)
    return jsonify(
        true_positives=tp, false_positives=fp,
        false_negatives=fn, true_negatives=tn,
        precision_pct=round(precision * 100, 1),
        recall_pct=round(recall * 100, 1),
        f1_pct=round(f1 * 100, 1),
        note="Planted anomalies exist only in the synthetic data; this is an evaluation aid.",
    )


# ---------- 7. GET /export/alerts.csv ----------
@app.get("/export/alerts.csv")
# or: @app.route("/export/alerts.csv")
def export_alerts():
    sql = ALERT_SQL + " ORDER BY a.risk_score DESC, a.alert_id"
    with engine.connect() as conn:
        rows = [clean(r) for r in conn.execute(text(sql)).mappings().all()]
    columns = ["alert_id", "transaction_id", "account_id", "transaction_datetime",
               "amount", "region", "rule_name", "risk_score", "review_status",
               "reason", "created_at"]
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=alerts.csv"})

@app.get("/ui")
# or: @app.route("/ui")
def ui_home():
    return render_template("index.html")

@app.get("/ui/alerts")
# or: @app.route("/ui/alerts")
def ui_alerts():
    return render_template("alerts.html")

@app.get("/ui/check")
# or: @app.route("/ui/check")
def ui_check():
    return render_template("check.html")

@app.get("/ui/transactions")
# or: @app.route("/ui/transactions")
def ui_transactions():
    return render_template("transactions.html")


@app.get("/ui/rules")
# or: @app.route("/ui/rules")
def ui_rules():
    return render_template("rules.html")


@app.get("/ui/audit")
# or: @app.route("/ui/audit")
def ui_audit():
    return render_template("audit.html")

@app.get("/ui/evaluation")
# or: @app.route("/ui/evaluation")
def ui_evaluation():
    return render_template("evaluation.html")

@app.get("/ui/account")
# or: @app.route("/ui/account")
def ui_account():
    return render_template("account.html")

if __name__ == "__main__":
    app.run(debug=True)

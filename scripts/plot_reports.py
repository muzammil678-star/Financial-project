import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import matplotlib.pyplot as plt
import pandas as pd

from db import engine

OUT = os.path.join(ROOT, "reports")
os.makedirs(OUT, exist_ok=True)

alerts = pd.read_sql("SELECT risk_score FROM alerts", engine)
alerts["risk_score"] = alerts["risk_score"].astype(float)

plt.figure(figsize=(7, 4))
plt.hist(alerts["risk_score"], bins=range(30, 115, 5), edgecolor="black")
plt.axvline(35, color="orange", linestyle="--", label="alert cut-off (35)")
plt.axvline(65, color="red", linestyle="--", label="HIGH cut-off (65)")
plt.xlabel("Risk score")
plt.ylabel("Number of alerts")
plt.title("Risk score distribution of alerts")
plt.legend()
plt.tight_layout()
plt.savefig(os.path.join(OUT, "risk_distribution.png"))
plt.show()

per_day = pd.read_sql(
    "SELECT DATE(t.transaction_datetime) AS alert_date, COUNT(*) AS alerts "
    "FROM alerts a JOIN transactions t ON t.transaction_id = a.transaction_id "
    "GROUP BY alert_date ORDER BY alert_date",
    engine,
)

plt.figure(figsize=(8, 4))
plt.plot(per_day["alert_date"].astype(str), per_day["alerts"], marker="o")
plt.xticks(rotation=90, fontsize=7)
plt.xlabel("Date")
plt.ylabel("Alerts")
plt.title("Alerts per day")
plt.tight_layout()
plt.savefig(os.path.join(OUT, "alert_trend.png"))
plt.show()

by_rule = pd.read_sql(
    "SELECT rule_name, COUNT(*) AS alerts FROM alerts "
    "GROUP BY rule_name ORDER BY alerts",
    engine,
)

plt.figure(figsize=(8, 4))
plt.barh(by_rule["rule_name"], by_rule["alerts"])
plt.xlabel("Alerts")
plt.title("Alerts by rule(s) triggered")
plt.tight_layout()
plt.savefig(os.path.join(OUT, "alerts_by_rule.png"))
plt.show()
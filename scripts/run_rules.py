import os

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()
engine = create_engine(
    f"mysql+pymysql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
    f"@{os.getenv('DB_HOST')}/{os.getenv('DB_NAME')}"
)

ALERT_CUTOFF = 35    # score needed to raise an alert
HIGH_CUTOFF = 65     # score for HIGH (any two rules together)

# 1. READ THE RULES FROM THE DATABASE
rules = pd.read_sql(
    "SELECT rule_name, weight, threshold FROM rule_config WHERE is_active = 1", engine)
rules["threshold"] = rules["threshold"].astype(float)
W = dict(zip(rules["rule_name"], rules["weight"]))
T = dict(zip(rules["rule_name"], rules["threshold"]))

# 2. LOAD TRANSACTIONS
df = pd.read_sql(
    "SELECT transaction_id, account_id, transaction_datetime, amount, region, "
    "is_planted_anomaly FROM transactions", engine)
df["amount"] = df["amount"].astype(float)
df = df.sort_values(["account_id", "transaction_datetime"]).reset_index(drop=True)
df["ts"] = (df["transaction_datetime"] - pd.Timestamp("1970-01-01")).dt.total_seconds()

# 3. RULE: UNUSUAL AMOUNT
g = df.groupby("account_id")["amount"]
std = g.transform("std").replace(0, np.nan)
df["z"] = ((df["amount"] - g.transform("mean")) / std).fillna(0)
f_amt = df["z"] >= T["UNUSUAL_AMOUNT"]

# 4. RULE: HIGH FREQUENCY (transactions by the account in the last 10 minutes)
ts = df["ts"].to_numpy()
freq = np.zeros(len(df), dtype=int)
for idx in df.groupby("account_id").indices.values():
    t = ts[idx]
    freq[idx] = (np.searchsorted(t, t + 600, side="right")
                 - np.searchsorted(t, t - 600, side="left"))
df["freq10"] = freq
f_freq = df["freq10"] >= T["HIGH_FREQUENCY"]

# 5. RULE: LOCATION CHANGE (region rarely used by this account)
region_count = df.groupby(["account_id", "region"])["amount"].transform("size")
acct_count = df.groupby("account_id")["amount"].transform("size")
df["region_share"] = region_count / acct_count
df["hour"] = df["transaction_datetime"].dt.hour
f_loc = (df["region_share"] < T["LOCATION_CHANGE"]) & (df["hour"] < 5)

# 6. SCORE AND REASONS
df["score"] = (f_amt * W["UNUSUAL_AMOUNT"]
               + f_freq * W["HIGH_FREQUENCY"]
               + f_loc * W["LOCATION_CHANGE"]).clip(upper=100)

n_amt = np.where(f_amt, "UNUSUAL_AMOUNT", "")
n_frq = np.where(f_freq, "HIGH_FREQUENCY", "")
n_loc = np.where(f_loc, "LOCATION_CHANGE", "")
df["rule_name"] = ["+".join(x for x in trio if x) for trio in zip(n_amt, n_frq, n_loc)]

r_amt = np.where(f_amt, "Amount " + df["z"].round(1).astype(str)
                 + " std devs above account average.", "")
r_frq = np.where(f_freq, df["freq10"].astype(str) + " transactions within 10 minutes.", "")
r_loc = np.where(f_loc, "Region " + df["region"] + " used in "
                 + (df["region_share"] * 100).round(1).astype(str)
                 + "% of account history.", "")
df["reason"] = [" ".join(x for x in trio if x) for trio in zip(r_amt, r_frq, r_loc)]

# 7. SAVE ALERTS (clears old ones first so you can re-run after tuning)
alerts = (df[df["score"] >= ALERT_CUTOFF]
          [["transaction_id", "rule_name", "score", "reason"]]
          .rename(columns={"score": "risk_score"}))
alerts["reason"] = alerts["reason"].str.slice(0, 255)

with engine.begin() as conn:
    conn.execute(text("DELETE FROM alerts"))
alerts.to_sql("alerts", engine, if_exists="append", index=False, chunksize=5000)

# 8. SCORECARD AGAINST THE PLANTED ANSWER KEY
flagged = df["score"] >= ALERT_CUTOFF
planted = df["is_planted_anomaly"] == 1
tp = int((flagged & planted).sum())
fp = int((flagged & ~planted).sum())
fn = int((~flagged & planted).sum())

print("Alerts created:", len(alerts))
print("HIGH alerts:", int((df["score"] >= HIGH_CUTOFF).sum()))
print("Caught planted anomalies (true positives):", tp)
print("Normal transactions flagged (false positives):", fp)
print("Planted anomalies missed (false negatives):", fn)
print("Precision:", round(tp / max(tp + fp, 1) * 100, 1), "%")
print("Recall:", round(tp / max(tp + fn, 1) * 100, 1), "%")
import os
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine

# 1. CONNECT
load_dotenv()
engine = create_engine(
    f"mysql+pymysql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
    f"@{os.getenv('DB_HOST')}/{os.getenv('DB_NAME')}"
)

# 2. SETTINGS
rng = np.random.default_rng(42)          # fixed seed: same data every run
N_CUSTOMERS = 200
DAYS = 30
START = datetime(2026, 9, 1)
REGIONS = ["North", "South", "East", "West", "Central"]
TYPES = ["purchase", "transfer", "withdrawal", "deposit"]
CHANNELS = ["online", "atm", "pos", "mobile"]
HOUR_W = np.array([1,1,1,1,1,2,4,6,8,8,8,8,8,8,8,8,8,8,8,7,6,4,2,1], float)
HOUR_W /= HOUR_W.sum()                   # daytime hours are more likely

# 3. CUSTOMERS AND ACCOUNTS
customers = pd.DataFrame({
    "customer_id": range(1, N_CUSTOMERS + 1),
    "customer_code": [f"CUST{i:04d}" for i in range(1, N_CUSTOMERS + 1)],
})

acct_rows = []
acct_id = 1
for cid in customers["customer_id"]:
    n_acc = 2 if rng.random() < 0.3 else 1
    for _ in range(n_acc):
        acct_rows.append({
            "account_id": acct_id,
            "customer_id": int(cid),
            "home": str(rng.choice(REGIONS)),
            "avg": float(rng.lognormal(4.0, 0.5)),   # typical amount
        })
        acct_id += 1
accounts = pd.DataFrame(acct_rows)


def make_time(day, hour):
    return START + timedelta(days=int(day), hours=int(hour),
                             minutes=int(rng.integers(0, 60)),
                             seconds=int(rng.integers(0, 60)))


rows = []

# 4. NORMAL TRANSACTIONS
for a in acct_rows:
    n = rng.poisson(150)
    days = rng.integers(0, DAYS, n)
    hours = rng.choice(24, size=n, p=HOUR_W)
    amounts = rng.lognormal(np.log(a["avg"]), 0.5, n).round(2)
    for d, h, amt in zip(days, hours, amounts):
        region = a["home"] if rng.random() < 0.95 else str(rng.choice(REGIONS))
        rows.append({
            "account_id": a["account_id"],
            "transaction_datetime": make_time(d, h),
            "amount": max(float(amt), 1.0),
            "transaction_type": str(rng.choice(TYPES, p=[0.6, 0.15, 0.15, 0.1])),
            "channel": str(rng.choice(CHANNELS)),
            "region": region,
            "is_planted_anomaly": 0,
        })

# 5. PLANTED ANOMALIES
# 5a. amount spikes: 8 to 15 times the account's normal amount
for _ in range(200):
    a = acct_rows[int(rng.integers(0, len(acct_rows)))]
    rows.append({
        "account_id": a["account_id"],
        "transaction_datetime": make_time(rng.integers(0, DAYS), rng.choice(24, p=HOUR_W)),
        "amount": round(a["avg"] * float(rng.uniform(8, 15)), 2),
        "transaction_type": "transfer",
        "channel": str(rng.choice(CHANNELS)),
        "region": a["home"],
        "is_planted_anomaly": 1,
    })

# 5b. location change: overseas, in the small hours
for _ in range(200):
    a = acct_rows[int(rng.integers(0, len(acct_rows)))]
    rows.append({
        "account_id": a["account_id"],
        "transaction_datetime": make_time(rng.integers(0, DAYS), rng.integers(0, 5)),
        "amount": round(a["avg"] * float(rng.uniform(2, 4)), 2),
        "transaction_type": "purchase",
        "channel": "online",
        "region": "Overseas",
        "is_planted_anomaly": 1,
    })

# 5c. frequency bursts: 6 transactions within 10 minutes
for _ in range(40):
    a = acct_rows[int(rng.integers(0, len(acct_rows)))]
    base = make_time(rng.integers(0, DAYS), rng.choice(24, p=HOUR_W))
    for k in range(6):
        rows.append({
            "account_id": a["account_id"],
            "transaction_datetime": base + timedelta(minutes=int(rng.integers(0, 10))),
            "amount": round(a["avg"] * float(rng.uniform(1, 2)), 2),
            "transaction_type": "purchase",
            "channel": "online",
            "region": a["home"],
            "is_planted_anomaly": 1,
        })

txns = pd.DataFrame(rows).sort_values("transaction_datetime").reset_index(drop=True)

# 6. SAVE TO MYSQL AND PRINT COUNTS
customers.to_sql("customers", engine, if_exists="append", index=False)
accounts[["account_id", "customer_id"]].to_sql("accounts", engine, if_exists="append", index=False)
txns.to_sql("transactions", engine, if_exists="append", index=False, chunksize=5000)

print("Customers:", len(customers))
print("Accounts:", len(accounts))
print("Transactions:", len(txns))
print("Planted anomalies:", int(txns["is_planted_anomaly"].sum()))
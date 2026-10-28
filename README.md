# Financial Risk Analysis and Rule-Based Fraud Detection System

Analyses synthetic financial transactions, calculates transparent risk indicators, and flags transactions that meet configurable anomaly rules for manual review.

**Flagged transactions are flagged for review. They are never labelled as confirmed fraud.** All data is synthetic; no real banking data is used.

## Tech stack
Python, Flask, MySQL, SQLAlchemy, pandas, NumPy, Matplotlib, Chart.js, plain HTML/CSS/JavaScript.

## Features
- REST API with 19 routes for transaction import, retrieval, alerts and reports
- Rule-based alerts for unusual amount, high frequency and location change
- Configurable risk score with documented weights (stored in the `rule_config` table)
- Review status and reason on every alert, with a review workflow in the web UI
- Risk distribution and alert trend reports, shown as live interactive charts
- Audit log (every data change) and access log (every request)
- Web pages: dashboard, alerts, transactions, check-a-transaction, rules, audit log

## Setup
1. Clone the repo and open it in VS Code.
2. Create the environment and install the libraries:
   ```
   python -m venv .venv
   source .venv/Scripts/activate
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and fill in your MySQL password. Set `DB_NAME=finance_db`.
4. In MySQL Workbench, run `schema.sql`, then `seed_rules.sql`.
5. Generate the synthetic data and the alerts:
   ```
   python scripts/generate_data.py
   python scripts/run_rules.py
   ```
6. Optional: regenerate the chart images:
   ```
   python scripts/plot_reports.py
   ```

## Running the app
1. Make sure the MySQL service is running.
2. Activate the environment: `source .venv/Scripts/activate`
3. Start the app: `python app.py`
4. Open `http://127.0.0.1:5000/ui`

Suggested demo order: Dashboard, Alerts (change a status), Check transaction (try region `Overseas` at `02:40` to see a HIGH result), Rules, Audit log.

## Web pages
| URL | Page |
|---|---|
| `/ui` | Dashboard: alert counts and live charts |
| `/ui/alerts` | Alerts table with review status changes |
| `/ui/transactions` | Browse and filter transactions |
| `/ui/check` | Check a new transaction |
| `/ui/rules` | Rules, weights and thresholds |
| `/ui/audit` | Audit log |

## API endpoints
| Method | URL | Purpose |
|---|---|---|
| GET | `/` | API name |
| GET | `/health` | Database connection check |
| GET | `/alerts` | List alerts (`?status=`, `?limit=`) |
| GET | `/alerts/<id>` | One alert |
| PATCH | `/alerts/<id>` | Change review status |
| GET | `/transactions` | List transactions (`?account_id=`, `?region=`, `?limit=`) |
| GET | `/transactions/<id>` | One transaction |
| POST | `/transactions` | Import and score a new transaction |
| GET | `/rules` | Rules, weights and thresholds |
| GET | `/reports/risk-distribution` | Alert counts by level and status |
| GET | `/reports/alert-trends` | Alerts per day |
| GET | `/audit-log` | Newest audit entries |
| GET | `/accounts` | List accounts |
| GET | `/accounts/<id>` | Account profile: spending stats, regions, alerts |
| GET | `/alerts/<id>/history` | Audit trail of one alert |
| PATCH | `/rules/<name>` | Change a rule's weight, threshold or active flag |
| GET | `/reports/alerts-by-rule` | Alert counts and average score per rule |
| GET | `/reports/evaluation` | Precision, recall and F1 from the planted anomalies |
| GET | `/export/alerts.csv` | Download alerts as CSV |

Callers may send an `X-User` header; it is recorded in the audit and access logs.

## Logs
- **Audit log** (`audit_log` table, page `/ui/audit`): records every change to a transaction or alert, with the user, action and details.
- **Access log** (`logs/access.log`): records every request with time, caller, method, URL and status code. This file is created when the app runs and is not committed to Git.

## Rules and risk score
| Rule | Weight | Fires when |
|---|---|---|
| UNUSUAL_AMOUNT | 40 | amount is 4 or more standard deviations above the account average |
| HIGH_FREQUENCY | 35 | 5 or more transactions by the account within 10 minutes |
| LOCATION_CHANGE | 35 | region used in under 1% of the account history, between 00:00 and 04:59 |

The risk score is the sum of the weights of the rules that fired, capped at 100. A score of 35 or more creates an alert; 65 or more is HIGH. The weights and thresholds live in the `rule_config` table and can be changed without editing code.

## Evaluation
The synthetic data contains planted anomalies (amount spikes, overseas purchases at night, bursts of transactions), which act as an answer key.

| Measure | Value |
|---|---|
| Alerts created | 670 (11 HIGH) |
| True positives | 552 |
| False positives | 118 |
| False negatives | 88 |
| True negatives | 38,335 |
| Precision | 82.4% |
| Recall | 86.2% |
| F1 score | 84.3% |

**False positives** are normal transactions that happen to look unusual, for example a large but legitimate purchase. They cost reviewer time. **False negatives** are anomalies the rules miss, mainly location changes that do not coincide with another rule, and accounts with little history.

## Limitations of rule-based detection
- Rules only catch patterns someone thought to write down; new patterns pass unnoticed.
- Thresholds were tuned on synthetic data whose anomalies follow simple patterns. Real data would need re-tuning.
- Raising thresholds reduces false positives but creates more false negatives.
- The frequency rule looks at transactions both before and after the current one, so the bulk script suits review of past data, not live scoring.
- Accounts with fewer than 10 transactions have no reliable baseline, so the amount and location rules are skipped for them.
- There is no user login; the `X-User` header is self-reported.

## Project structure
```
app.py               Flask app: API routes and page routes
db.py                Database connection
schema.sql           Creates the database tables
seed_rules.sql       Inserts the three rules
scripts/             generate_data.py, run_rules.py, plot_reports.py
templates/           HTML pages
static/              CSS, JavaScript, Chart.js and chart images
requirements.txt     Python libraries
.env.example         Template for your database settings
```

## Team
Shaik Muzammil (Team Lead)/104

Pathan Kamal Ahmed/107

Penumala Phani Babu/103

Gokul Sai Ram/88

Sake Akshaya/106


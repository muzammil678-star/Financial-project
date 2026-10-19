# Financial Risk Analysis and Risk Management

Small database + API backend built with Flask and MySQL.


## Team members are:
   Shaik Muzammil
   P.Phani Babu
   Pathan Kamal Ahmed
   Sai Ram
   Sake Akshaya


## Setup  For teammates only
1. Install MySQL Server and MySQL Workbench
2. Create the database in Workbench: `CREATE DATABASE finance_db;`
3. Create a virtual environment: `python -m venv .venv`
4. Activate it (Git Bash on Windows): `source .venv/Scripts/activate`
5. Install libraries: `pip install -r requirements.txt`
6. Copy `.env.example` to a new file named `.env` and fill in your MySQL username and password
7. Run the table definitions in the `schema` folder (open the .sql file in Workbench and click the lightning bolt)
8. Start the app: `python app.py`
9. Open http://127.0.0.1:5000/ in your browser. You should see `{"status":"API is running"}`

## Team mates Notice this

- Never push directly to `main`. Create a branch, push it, and open a Pull Request
- Never commit `.env`
- Pull the latest `main` before starting new work
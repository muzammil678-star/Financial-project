from flask import Flask, jsonify
from db import get_connection

app = Flask(__name__)


@app.route("/")
def home():
    return jsonify({"status": "API is running"})


@app.route("/health/db")
def db_health():
    conn = get_connection()
    conn.close()
    return jsonify({"database": "connected"})


if __name__ == "__main__":
    app.run(debug=True)
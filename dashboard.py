import os
import re
from flask import Flask, render_template, request, redirect, url_for, session
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from dotenv import load_dotenv

# =====================
# CONFIG
# =====================

load_dotenv()

LOG_FILE = "arb_bot.log"
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD")
SECRET_KEY = os.getenv("FLASK_SECRET_KEY")

app = Flask(__name__)
app.secret_key = SECRET_KEY

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per hour"]
)

# =====================
# HELPERS
# =====================

def read_logs(lines=200):
    if not os.path.exists(LOG_FILE):
        return []
    with open(LOG_FILE, "r") as f:
        return f.readlines()[-lines:][::-1]

def extract_trades(logs):
    trades = []
    for line in logs:
        if "ARB FOUND" in line:
            trades.append(line.strip())
    return trades

def estimate_profit(logs):
    profit = 0.0
    for line in logs:
        if "PROFIT +" in line:
            m = re.search(r"\+([0-9.]+)", line)
            if m:
                profit += float(m.group(1))
    return round(profit, 4)

def login_required(fn):
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    wrapper.__name__ = fn.__name__
    return wrapper

# =====================
# ROUTES
# =====================

@app.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def login():
    error = None
    if request.method == "POST":
        if request.form.get("password") == DASHBOARD_PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("index"))
        error = "Invalid password"
    return render_template("login.html", error=error)

@app.route("/logout")
@limiter.limit("10 per minute")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/")
@login_required
@limiter.limit("60 per minute")
def index():
    logs = read_logs()
    trades = extract_trades(logs)
    profit = estimate_profit(logs)

    return render_template(
        "index.html",
        logs=logs,
        trades=trades,
        profit=profit
    )

@app.errorhandler(429)
def ratelimit_handler(e):
    return "Too many requests", 429

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

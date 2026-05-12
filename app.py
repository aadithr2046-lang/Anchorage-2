"""
app.py — Flask backend for Anchorage 2026 IPL Prediction Contest
Deployment: Render.com
Run locally:  flask run
Run on Render: gunicorn app:app
"""

import re
import logging
from datetime import datetime, timezone

import pymysql
import pymysql.cursors
from flask import Flask, request, jsonify, g
from flask_cors import CORS

# ── App setup ────────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

# ── Database config ───────────────────────────────────────────────────────────
DB_CONFIG = {
    "host":        "yamabiko.proxy.rlwy.net",
    "port":        28286,
    "user":        "root",
    "password":    "GAwExjueEjqyWMnhYaDFgIqxZzwPHObU",
    "database":    "anchorage_ipl",
    "cursorclass": pymysql.cursors.DictCursor,
    "autocommit":  False,
    "connect_timeout": 10,
}

# ── Contest deadline: 31 May 2026, 6 PM IST (= 12:30 UTC) ───────────────────
CONTEST_DEADLINE = datetime(2026, 5, 31, 12, 30, 0, tzinfo=timezone.utc)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ── Database helpers ──────────────────────────────────────────────────────────
def get_db() -> pymysql.connections.Connection:
    """Return a per-request MySQL connection stored on Flask's g object."""
    if "db" not in g:
        g.db = pymysql.connect(**DB_CONFIG)
    return g.db


@app.teardown_appcontext
def close_db(error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


# ── Validation helpers ────────────────────────────────────────────────────────
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_PHONE_RE = re.compile(r"^[\d\s\+\-\(\)]{7,20}$")


def validate_payload(data: dict) -> list:
    errors = []

    name = (data.get("name") or "").strip()
    if not name:
        errors.append("name is required")

    email = (data.get("email") or "").strip().lower()
    if not _EMAIL_RE.match(email):
        errors.append("valid email is required")

    phone = (data.get("phone") or "").strip()
    if not _PHONE_RE.match(phone):
        errors.append("valid phone number is required")

    year = (data.get("year") or "").strip()
    if not year:
        errors.append("year of study is required")

    dept = (data.get("dept") or "").strip()
    if not dept:
        errors.append("department is required")

    team = (data.get("team") or "").strip().lower()
    if team not in ("team1", "team2"):
        errors.append("team must be 'team1' or 'team2'")

    return errors


# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/api/ipl/register", methods=["POST"])
def register():
    # 1. Deadline check
    now = datetime.now(tz=timezone.utc)
    if now >= CONTEST_DEADLINE:
        return jsonify(success=False, message="Contest has closed. No further entries accepted."), 403

    # 2. Parse JSON
    data = request.get_json(silent=True)
    if not data:
        return jsonify(success=False, message="Invalid JSON body."), 400

    # 3. Validate
    errors = validate_payload(data)
    if errors:
        return jsonify(success=False, message="; ".join(errors)), 422

    # 4. Normalise
    name          = data["name"].strip()
    email         = data["email"].strip().lower()
    phone         = data["phone"].strip()
    year          = data["year"].strip()
    dept          = data["dept"].strip()
    team          = data["team"].strip().lower()
    registered_at = now.strftime("%Y-%m-%d %H:%M:%S")

    # 5. Insert
    db = get_db()
    try:
        with db.cursor() as cursor:
            cursor.execute(
                """INSERT INTO registrations (name, email, phone, year, dept, team, registered_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (name, email, phone, year, dept, team, registered_at),
            )
        db.commit()
        logger.info("Registered: %s → %s", email, team)
    except pymysql.err.IntegrityError:
        db.rollback()
        return jsonify(success=False, message="This email address is already registered."), 409
    except Exception as e:
        db.rollback()
        logger.error("DB error: %s", e)
        return jsonify(success=False, message="Database error. Please try again."), 500

    return jsonify(success=True, message="Prediction recorded!"), 201


@app.route("/api/ipl/results", methods=["GET"])
def results():
    db = get_db()
    with db.cursor() as cursor:
        cursor.execute("SELECT team, COUNT(*) AS votes FROM registrations GROUP BY team")
        rows = cursor.fetchall()
    return jsonify(success=True, results={row["team"]: row["votes"] for row in rows})


@app.route("/api/ipl/entries", methods=["GET"])
def entries():
    db = get_db()
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT id, name, email, phone, year, dept, team, registered_at FROM registrations ORDER BY id"
        )
        rows = cursor.fetchall()
    return jsonify(success=True, entries=rows)


@app.route("/health", methods=["GET"])
def health():
    """Render pings this to check if the service is alive."""
    try:
        db = get_db()
        with db.cursor() as cursor:
            cursor.execute("SELECT 1")
        return jsonify(status="ok", db="connected"), 200
    except Exception as e:
        logger.error("Health check failed: %s", e)
        return jsonify(status="error", db="disconnected"), 500


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=5000)

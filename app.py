"""
IPL Final 2026 — Prediction Contest
Flask Backend  (app.py)

Install:
  pip install flask flask-cors pymysql python-dotenv

Run (dev):
  python app.py

Run (prod, gunicorn):
  gunicorn -w 4 -b 0.0.0.0:5000 app:app
"""

import os
import re
from datetime import datetime, timezone

import pymysql
import pymysql.cursors
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

load_dotenv()

app = Flask(__name__)
CORS(app, origins=os.getenv("ALLOWED_ORIGINS", "*"))

# ── CONFIG ────────────────────────────────────────────────────
DB_CONFIG = {
    "host":        os.getenv("DB_HOST", "localhost"),
    "port":        int(os.getenv("DB_PORT", 3306)),
    "user":        os.getenv("DB_USER", "root"),
    "password":    os.getenv("DB_PASS", ""),
    "db":          os.getenv("DB_NAME", "ipl_contest"),
    "charset":     "utf8mb4",
    "cursorclass": pymysql.cursors.DictCursor,
    "autocommit":  True,
}

# Contest closes at 2026-05-31 18:00 IST  (= 12:30 UTC)
DEADLINE_UTC = datetime(2026, 5, 31, 12, 30, 0, tzinfo=timezone.utc)

# ================================================================
# ▼▼▼  EDIT THIS BLOCK ONLY — keep in sync with frontend FINAL_TEAMS
#       team value = whatever abbr you set in the HTML FINAL_TEAMS array
# ================================================================
VALID_TEAMS = {
    "TEAM1": "Team 1 Full Name",   # e.g. "RCB": "Royal Challengers Bengaluru"
    "TEAM2": "Team 2 Full Name",   # e.g. "PBKS": "Punjab Kings"
}
# ================================================================
# ▲▲▲  END CONFIG
# ================================================================


# ── HELPERS ──────────────────────────────────────────────────
def get_db():
    return pymysql.connect(**DB_CONFIG)


def is_valid_email(email: str) -> bool:
    return bool(re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", email))


def normalise_phone(phone: str) -> str:
    """Strip spaces, dashes, dots so +91-98765 43210 == +919876543210."""
    return re.sub(r"[\s\-.()\u00a0]", "", phone)


def err(msg: str, code: int = 400):
    return jsonify({"ok": False, "error": msg}), code


# ── ROUTES ───────────────────────────────────────────────────

@app.route("/")
@app.route("/index.html")
def index():
    """Serve the main landing page with the 'Join IPL Contest' button."""
    return send_from_directory("static", "index.html")


@app.route("/register")
@app.route("/ipl.html")
def register_page():
    """Serve the registration / prediction form."""
    return send_from_directory("static", "ipl.html")


@app.route("/api/teams", methods=["GET"])
def get_teams():
    """
    GET /api/teams
    Returns the valid team list so the frontend can optionally
    pull it dynamically instead of hardcoding.
    """
    teams = [{"abbr": k, "name": v} for k, v in VALID_TEAMS.items()]
    return jsonify({"ok": True, "teams": teams})


@app.route("/api/register", methods=["POST"])
def register():
    """
    POST /api/register
    Body (JSON):
      name, email, phone, year, dept, team
    """
    # ── 1. Deadline guard
    if datetime.now(timezone.utc) >= DEADLINE_UTC:
        return err("Contest is closed. Predictions ended on 31 May 2026 at 6 PM IST.", 403)

    data = request.get_json(silent=True)
    if not data:
        return err("Invalid JSON body.")

    # ── 2. Sanitise & validate
    name  = str(data.get("name",  "")).strip()[:120]
    email = str(data.get("email", "")).strip().lower()[:254]
    phone = normalise_phone(str(data.get("phone", "")))[:20]
    year  = str(data.get("year",  "")).strip()[:30]
    dept  = str(data.get("dept",  "")).strip()[:120]
    team  = str(data.get("team",  "")).strip().upper()[:10]

    if not name:
        return err("Name is required.")
    if not is_valid_email(email):
        return err("A valid email address is required.")
    if not phone:
        return err("Phone number is required.")
    if not year:
        return err("Year of study is required.")
    if not dept:
        return err("Department is required.")

    # ── 3. Team validation — driven by VALID_TEAMS dict, not hardcoded strings
    if team not in VALID_TEAMS:
        return err(
            f"Invalid team. Must be one of: {', '.join(sorted(VALID_TEAMS.keys()))}."
        )

    ip = request.headers.get("X-Forwarded-For", request.remote_addr)

    # ── 4. Duplicate check — email AND phone
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT email, phone FROM registrations WHERE email = %s OR phone = %s LIMIT 1",
                (email, phone),
            )
            existing = cur.fetchone()

        if existing:
            conn.close()
            if existing["email"] == email:
                return err("This email address has already been registered.", 409)
            if existing["phone"] == phone:
                return err("This phone number has already been registered.", 409)

    except Exception as exc:
        app.logger.error("DB duplicate-check error: %s", exc)
        return err("Database error. Please try again.", 500)

    # ── 5. Insert — store full team name alongside abbr for readability
    team_name = VALID_TEAMS[team]
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO registrations
                  (name, email, phone, year, dept, team, team_name, ip_address)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (name, email, phone, year, dept, team, team_name, ip),
            )
        conn.close()

    except pymysql.err.IntegrityError as exc:
        msg = str(exc).lower()
        if "uq_phone" in msg or "'phone'" in msg:
            return err("This phone number has already been registered.", 409)
        return err("This email address has already been registered.", 409)

    except Exception as exc:
        app.logger.error("DB insert error: %s", exc)
        return err("Database error. Please try again.", 500)

    return jsonify({
        "ok":       True,
        "message":  "Prediction locked! Good luck.",
        "team":     team,
        "teamName": team_name,
    }), 201


@app.route("/api/results", methods=["GET"])
def results():
    """GET /api/results — vote tally per team."""
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT team, team_name, COUNT(*) AS votes
                FROM registrations
                GROUP BY team, team_name
                ORDER BY votes DESC
                """
            )
            rows = cur.fetchall()
        conn.close()
    except Exception as exc:
        app.logger.error("DB error: %s", exc)
        return err("Could not fetch results.", 500)

    return jsonify({"ok": True, "results": rows})


@app.route("/api/admin/entries", methods=["GET"])
def admin_entries():
    """GET /api/admin/entries?secret=YOUR_SECRET — full export."""
    secret = os.getenv("ADMIN_SECRET", "changeme")
    if request.args.get("secret") != secret:
        return err("Forbidden.", 403)

    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, email, phone, year, dept,
                       team, team_name, created_at
                FROM registrations
                ORDER BY created_at DESC
                """
            )
            rows = cur.fetchall()
        conn.close()
        for r in rows:
            if isinstance(r.get("created_at"), datetime):
                r["created_at"] = r["created_at"].isoformat()
    except Exception as exc:
        app.logger.error("DB error: %s", exc)
        return err("Could not fetch entries.", 500)

    return jsonify({"ok": True, "count": len(rows), "entries": rows})


# ── RUN ──────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", 5000)),
        debug=os.getenv("FLASK_DEBUG", "false").lower() == "true",
    )

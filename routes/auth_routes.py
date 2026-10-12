"""Auth routes — register, login, me, forgot/reset password"""

import os
import secrets
from datetime import datetime, timedelta, timezone
from flask import Blueprint, request, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from flask_jwt_extended import create_access_token, jwt_required, get_jwt_identity
from database import get_db
from mailer import send_email, render_password_reset_email

auth_bp = Blueprint("auth", __name__)

APP_BASE_URL = os.environ.get("APP_BASE_URL", "")
RESET_TOKEN_VALID_HOURS = 1


@auth_bp.route("/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}
    name     = (data.get("name")     or "").strip()
    email    = (data.get("email")    or "").strip().lower()
    password = (data.get("password") or "").strip()
    company  = (data.get("company")  or "").strip()
    phone    = (data.get("phone")    or "").strip()
    role     = "client"  # public self-registration can only ever create client accounts;
                          # staff accounts are created by an admin (Users/Clients tab), never self-registered.

    if not name or not email or not password or not company:
        return jsonify(error="Name, email, company, and password are required."), 400
    if len(password) < 8:
        return jsonify(error="Password must be at least 8 characters."), 400

    db = get_db()
    cur = db.cursor()
    try:
        # Find or create the matching client company so this contact shows up
        # under the Clients tab, not just as a standalone user.
        cur.execute("SELECT id FROM clients WHERE LOWER(name) = LOWER(%s)", (company,))
        existing_client = cur.fetchone()
        if existing_client:
            client_id = existing_client["id"]
        else:
            cur.execute("INSERT INTO clients (name) VALUES (%s) RETURNING id", (company,))
            client_id = cur.fetchone()["id"]

        cur.execute(
            "INSERT INTO users (name, email, password, role, company, phone, client_id) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (name, email, generate_password_hash(password), role, company, phone, client_id),
        )
        db.commit()
    except Exception:
        db.rollback()
        return jsonify(error="Email already registered."), 409
    finally:
        cur.close()
        db.close()

    return jsonify(message="Account created. Please log in."), 201


@auth_bp.route("/login", methods=["POST"])
def login():
    data     = request.get_json(silent=True) or {}
    email    = (data.get("email")    or "").strip().lower()
    password = (data.get("password") or "").strip()

    if not email or not password:
        return jsonify(error="Email and password required."), 400

    db  = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM users WHERE email = %s", (email,))
    user = cur.fetchone()
    cur.close()
    db.close()

    if not user or not check_password_hash(user["password"], password):
        return jsonify(error="Invalid credentials."), 401

    token = create_access_token(identity=str(user["id"]))
    return jsonify(
        token=token,
        user=dict(id=user["id"], name=user["name"],
                  email=user["email"], role=user["role"],
                  company=user["company"])
    )


@auth_bp.route("/me", methods=["GET"])
@jwt_required()
def me():
    uid = int(get_jwt_identity())
    db  = get_db()
    cur = db.cursor()
    cur.execute("SELECT id,name,email,role,company,phone,created_at FROM users WHERE id=%s", (uid,))
    user = cur.fetchone()
    cur.close()
    db.close()
    if not user:
        return jsonify(error="User not found."), 404
    return jsonify(dict(user))


@auth_bp.route("/forgot-password", methods=["POST"])
def forgot_password():
    data  = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()

    # Always return the same generic response whether or not the email exists,
    # so this endpoint can't be used to discover which emails are registered.
    generic_response = jsonify(
        message="A password reset link has been sent to your email."
    )

    if not email:
        return generic_response

    db  = get_db()
    cur = db.cursor()
    cur.execute("SELECT id, name, email FROM users WHERE email=%s", (email,))
    user = cur.fetchone()

    if user:
        reset_token = secrets.token_urlsafe(32)
        expires_at  = datetime.now(timezone.utc) + timedelta(hours=RESET_TOKEN_VALID_HOURS)
        cur.execute(
            "UPDATE users SET reset_token=%s, reset_token_expires=%s WHERE id=%s",
            (reset_token, expires_at, user["id"])
        )
        db.commit()

        reset_link = f"{APP_BASE_URL}/reset-password?token={reset_token}" if APP_BASE_URL else None
        html_body = render_password_reset_email(
            user_name=user["name"],
            reset_link=reset_link,
            valid_hours=RESET_TOKEN_VALID_HOURS,
        )
        send_email(user["email"], user["name"], "Reset Your Password – TIA Ticketing", html_body)

    cur.close()
    db.close()
    return generic_response


@auth_bp.route("/reset-password", methods=["POST"])
def reset_password():
    data     = request.get_json(silent=True) or {}
    token    = (data.get("token")    or "").strip()
    password = (data.get("password") or "").strip()

    if not token or not password:
        return jsonify(error="Token and new password are required."), 400
    if len(password) < 8:
        return jsonify(error="Password must be at least 8 characters."), 400

    db  = get_db()
    cur = db.cursor()
    cur.execute(
        "SELECT id, reset_token_expires FROM users WHERE reset_token=%s",
        (token,)
    )
    user = cur.fetchone()

    if not user:
        cur.close(); db.close()
        return jsonify(error="This reset link is invalid or has already been used."), 400

    expires_at = user["reset_token_expires"]
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > expires_at:
        cur.close(); db.close()
        return jsonify(error="This reset link has expired. Please request a new one."), 400

    cur.execute(
        "UPDATE users SET password=%s, reset_token=NULL, reset_token_expires=NULL WHERE id=%s",
        (generate_password_hash(password), user["id"])
    )
    db.commit()
    cur.close()
    db.close()
    return jsonify(message="Your password has been reset. Please log in.")

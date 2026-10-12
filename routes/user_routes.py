"""User management routes (admin only for most)"""

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from werkzeug.security import generate_password_hash
from database import get_db

user_bp = Blueprint("users", __name__)


@user_bp.route("", methods=["POST"])
@jwt_required()
def create_user():
    uid = int(get_jwt_identity())
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    password = (data.get("password") or "").strip()
    company = (data.get("company") or "").strip()
    phone = (data.get("phone") or "").strip()
    role = (data.get("role") or "client").strip().lower()

    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT role FROM users WHERE id=%s", (uid,))
    me = cur.fetchone()
    if me["role"] not in ("admin", "technician"):
        cur.close(); db.close(); return jsonify(error="Access denied."), 403
    if role not in ("client", "agent", "technician") and me["role"] != "admin":
        cur.close(); db.close(); return jsonify(error="Only admins can create admins."), 403
    if role not in ("client", "agent", "technician", "admin"):
        role = "client"
    if not name or not email or not password:
        cur.close(); db.close(); return jsonify(error="Name, email and password are required."), 400
    if len(password) < 8:
        cur.close(); db.close(); return jsonify(error="Password must be at least 8 characters."), 400

    try:
        cur.execute(
            "INSERT INTO users (name, email, password, role, company, phone) VALUES (%s,%s,%s,%s,%s,%s)",
            (name, email, generate_password_hash(password), role, company, phone),
        )
        db.commit()
    except Exception:
        db.rollback()
        cur.close(); db.close(); return jsonify(error="Email already registered."), 409

    cur.execute("SELECT id,name,email,role,company,phone,created_at FROM users WHERE email=%s", (email,))
    user = cur.fetchone()
    cur.close()
    db.close()
    return jsonify(dict(user)), 201


@user_bp.route("", methods=["GET"])
@jwt_required()
def list_users():
    uid  = int(get_jwt_identity())
    db   = get_db()
    cur  = db.cursor()
    cur.execute("SELECT role FROM users WHERE id=%s", (uid,))
    user = cur.fetchone()
    if user["role"] not in ("admin", "agent", "technician"):
        cur.close(); db.close(); return jsonify(error="Access denied."), 403

    role   = request.args.get("role", "")
    search = request.args.get("q", "")
    q = "SELECT id,name,email,role,company,phone,created_at FROM users WHERE 1=1"
    params = []
    if role:   q += " AND role=%s";                    params.append(role)
    if search: q += " AND (name ILIKE %s OR email ILIKE %s)"; params += [f"%{search}%", f"%{search}%"]
    q += " ORDER BY created_at DESC"
    cur.execute(q, params)
    rows = cur.fetchall()
    cur.close()
    db.close()
    return jsonify(users=[dict(r) for r in rows])


@user_bp.route("/<int:user_id>", methods=["GET"])
@jwt_required()
def get_user(user_id):
    uid  = int(get_jwt_identity())
    db   = get_db()
    cur  = db.cursor()
    cur.execute("SELECT role FROM users WHERE id=%s", (uid,))
    me = cur.fetchone()
    if me["role"] not in ("admin",) and uid != user_id:
        cur.close(); db.close(); return jsonify(error="Access denied."), 403
    cur.execute(
        "SELECT id,name,email,role,company,phone,created_at FROM users WHERE id=%s", (user_id,)
    )
    user = cur.fetchone()
    cur.close()
    db.close()
    if not user: return jsonify(error="User not found."), 404
    return jsonify(dict(user))


@user_bp.route("/<int:user_id>", methods=["PUT"])
@jwt_required()
def update_user(user_id):
    uid  = int(get_jwt_identity())
    data = request.get_json(silent=True) or {}
    db   = get_db()
    cur  = db.cursor()
    cur.execute("SELECT role FROM users WHERE id=%s", (uid,))
    me = cur.fetchone()

    if me["role"] not in ("admin",) and uid != user_id:
        cur.close(); db.close(); return jsonify(error="Access denied."), 403

    fields, params = [], []
    for f in ("name", "company", "phone"):
        if f in data:
            fields.append(f"{f}=%s"); params.append(data[f])
    if "role" in data and me["role"] == "admin":
        fields.append("role=%s"); params.append(data["role"])
    if "password" in data:
        if len(data["password"]) < 8:
            cur.close(); db.close(); return jsonify(error="Password must be ≥ 8 characters."), 400
        fields.append("password=%s"); params.append(generate_password_hash(data["password"]))

    if fields:
        params.append(user_id)
        cur.execute(f"UPDATE users SET {', '.join(fields)} WHERE id=%s", params)
        db.commit()

    cur.execute(
        "SELECT id,name,email,role,company,phone,created_at FROM users WHERE id=%s", (user_id,)
    )
    user = cur.fetchone()
    cur.close()
    db.close()
    return jsonify(dict(user))


@user_bp.route("/<int:user_id>", methods=["DELETE"])
@jwt_required()
def delete_user(user_id):
    uid  = int(get_jwt_identity())
    db   = get_db()
    cur  = db.cursor()
    cur.execute("SELECT role FROM users WHERE id=%s", (uid,))
    me = cur.fetchone()
    if me["role"] != "admin":
        cur.close(); db.close(); return jsonify(error="Admins only."), 403
    if uid == user_id:
        cur.close(); db.close(); return jsonify(error="Cannot delete yourself."), 400
    cur.execute("DELETE FROM users WHERE id=%s", (user_id,))
    db.commit()
    cur.close()
    db.close()
    return jsonify(message="User deleted.")


@user_bp.route("/notifications", methods=["GET"])
@jwt_required()
def get_notifications():
    uid = int(get_jwt_identity())
    db  = get_db()
    cur = db.cursor()
    cur.execute(
        "SELECT * FROM notifications WHERE user_id=%s ORDER BY created_at DESC LIMIT 50", (uid,)
    )
    rows = cur.fetchall()
    cur.close()
    db.close()
    return jsonify(notifications=[dict(r) for r in rows])


@user_bp.route("/notifications/read", methods=["POST"])
@jwt_required()
def mark_notifications_read():
    uid = int(get_jwt_identity())
    db  = get_db()
    cur = db.cursor()
    cur.execute("UPDATE notifications SET is_read=1 WHERE user_id=%s", (uid,))
    db.commit()
    cur.close()
    db.close()
    return jsonify(message="All marked as read.")

"""Client (company) routes — staff manage companies and their contacts."""

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from werkzeug.security import generate_password_hash
from database import get_db

client_bp = Blueprint("clients", __name__)

STAFF_ROLES = ("admin", "agent", "technician")


def _require_staff(cur, uid):
    cur.execute("SELECT role FROM users WHERE id=%s", (uid,))
    user = cur.fetchone()
    if not user or user["role"] not in STAFF_ROLES:
        return None
    return user


@client_bp.route("", methods=["GET"])
@jwt_required()
def list_clients():
    uid = int(get_jwt_identity())
    db  = get_db()
    cur = db.cursor()
    if not _require_staff(cur, uid):
        cur.close(); db.close(); return jsonify(error="Access denied."), 403

    search = request.args.get("q", "")
    q = """SELECT c.*, COUNT(u.id) as contact_count
           FROM clients c LEFT JOIN users u ON u.client_id = c.id
           WHERE 1=1"""
    params = []
    if search:
        q += " AND c.name ILIKE %s"; params.append(f"%{search}%")
    q += " GROUP BY c.id ORDER BY c.name ASC"
    cur.execute(q, params)
    rows = cur.fetchall()
    cur.close()
    db.close()
    return jsonify(clients=[dict(r) for r in rows])


@client_bp.route("", methods=["POST"])
@jwt_required()
def create_client():
    uid  = int(get_jwt_identity())
    data = request.get_json(silent=True) or {}
    name  = (data.get("name")  or "").strip()
    notes = (data.get("notes") or "").strip()

    db  = get_db()
    cur = db.cursor()
    if not _require_staff(cur, uid):
        cur.close(); db.close(); return jsonify(error="Access denied."), 403
    if not name:
        cur.close(); db.close(); return jsonify(error="Company name is required."), 400

    try:
        cur.execute(
            "INSERT INTO clients (name, notes) VALUES (%s,%s) RETURNING id",
            (name, notes)
        )
        client_id = cur.fetchone()["id"]
        db.commit()
    except Exception:
        db.rollback()
        cur.close(); db.close(); return jsonify(error="A client with that name already exists."), 409

    cur.execute("SELECT * FROM clients WHERE id=%s", (client_id,))
    client = cur.fetchone()
    cur.close()
    db.close()
    return jsonify(dict(client)), 201


@client_bp.route("/<int:client_id>", methods=["GET"])
@jwt_required()
def get_client(client_id):
    uid = int(get_jwt_identity())
    db  = get_db()
    cur = db.cursor()
    if not _require_staff(cur, uid):
        cur.close(); db.close(); return jsonify(error="Access denied."), 403

    cur.execute("SELECT * FROM clients WHERE id=%s", (client_id,))
    client = cur.fetchone()
    if not client:
        cur.close(); db.close(); return jsonify(error="Client not found."), 404

    cur.execute(
        "SELECT id,name,email,phone,created_at FROM users WHERE client_id=%s ORDER BY name ASC",
        (client_id,)
    )
    contacts = cur.fetchall()
    cur.close()
    db.close()
    result = dict(client)
    result["contacts"] = [dict(c) for c in contacts]
    return jsonify(result)


@client_bp.route("/<int:client_id>", methods=["PUT"])
@jwt_required()
def update_client(client_id):
    uid  = int(get_jwt_identity())
    data = request.get_json(silent=True) or {}
    db   = get_db()
    cur  = db.cursor()
    if not _require_staff(cur, uid):
        cur.close(); db.close(); return jsonify(error="Access denied."), 403

    fields, params = [], []
    if "name"  in data: fields.append("name = %s");  params.append(data["name"].strip())
    if "notes" in data: fields.append("notes = %s"); params.append(data["notes"].strip())

    if fields:
        params.append(client_id)
        try:
            cur.execute(f"UPDATE clients SET {', '.join(fields)} WHERE id=%s", params)
            db.commit()
        except Exception:
            db.rollback()
            cur.close(); db.close(); return jsonify(error="A client with that name already exists."), 409

    cur.execute("SELECT * FROM clients WHERE id=%s", (client_id,))
    client = cur.fetchone()
    cur.close()
    db.close()
    if not client:
        return jsonify(error="Client not found."), 404
    return jsonify(dict(client))


@client_bp.route("/<int:client_id>", methods=["DELETE"])
@jwt_required()
def delete_client(client_id):
    uid = int(get_jwt_identity())
    db  = get_db()
    cur = db.cursor()
    cur.execute("SELECT role FROM users WHERE id=%s", (uid,))
    user = cur.fetchone()
    if not user or user["role"] != "admin":
        cur.close(); db.close(); return jsonify(error="Admins only."), 403

    cur.execute("SELECT COUNT(*) as c FROM users WHERE client_id=%s", (client_id,))
    if cur.fetchone()["c"] > 0:
        cur.close(); db.close()
        return jsonify(error="Cannot delete a client that still has contacts. Remove or reassign contacts first."), 400

    cur.execute("DELETE FROM clients WHERE id=%s", (client_id,))
    db.commit()
    cur.close()
    db.close()
    return jsonify(message="Client deleted.")


@client_bp.route("/<int:client_id>/users", methods=["POST"])
@jwt_required()
def add_client_contact(client_id):
    uid  = int(get_jwt_identity())
    data = request.get_json(silent=True) or {}
    db   = get_db()
    cur  = db.cursor()
    if not _require_staff(cur, uid):
        cur.close(); db.close(); return jsonify(error="Access denied."), 403

    cur.execute("SELECT * FROM clients WHERE id=%s", (client_id,))
    client = cur.fetchone()
    if not client:
        cur.close(); db.close(); return jsonify(error="Client not found."), 404

    name     = (data.get("name")     or "").strip()
    email    = (data.get("email")    or "").strip().lower()
    phone    = (data.get("phone")    or "").strip()
    password = (data.get("password") or "").strip()

    if not name or not email or not password:
        cur.close(); db.close(); return jsonify(error="Name, email and password are required."), 400
    if len(password) < 8:
        cur.close(); db.close(); return jsonify(error="Password must be at least 8 characters."), 400

    try:
        cur.execute(
            """INSERT INTO users (name, email, password, role, company, phone, client_id)
               VALUES (%s,%s,%s,'client',%s,%s,%s)""",
            (name, email, generate_password_hash(password), client["name"], phone, client_id)
        )
        db.commit()
    except Exception:
        db.rollback()
        cur.close(); db.close(); return jsonify(error="Email already registered."), 409

    cur.execute(
        "SELECT id,name,email,role,company,phone,client_id,created_at FROM users WHERE email=%s",
        (email,)
    )
    contact = cur.fetchone()
    cur.close()
    db.close()
    return jsonify(dict(contact)), 201

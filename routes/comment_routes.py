"""Comment routes"""

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from database import get_db, log_action

comment_bp = Blueprint("comments", __name__)

STAFF_ROLES = ("admin", "agent", "technician")


@comment_bp.route("/<int:ticket_id>", methods=["POST"])
@jwt_required()
def add_comment(ticket_id):
    uid  = int(get_jwt_identity())
    data = request.get_json(silent=True) or {}
    body = (data.get("body") or "").strip()

    if not body:
        return jsonify(error="Comment body is required."), 400

    db   = get_db()
    cur  = db.cursor()
    cur.execute("SELECT role FROM users WHERE id=%s", (uid,))
    user = cur.fetchone()
    cur.execute("SELECT * FROM tickets WHERE id=%s", (ticket_id,))
    t = cur.fetchone()

    if not t:
        cur.close(); db.close(); return jsonify(error="Ticket not found."), 404
    if user["role"] == "client" and t["created_by"] != uid:
        cur.close(); db.close(); return jsonify(error="Access denied."), 403

    is_internal = int(bool(data.get("is_internal"))) if user["role"] in STAFF_ROLES else 0

    cur.execute(
        "INSERT INTO comments (ticket_id, user_id, body, is_internal) VALUES (%s,%s,%s,%s) RETURNING id",
        (ticket_id, uid, body, is_internal)
    )
    comment_id = cur.fetchone()["id"]

    # The first staff reply on a ticket satisfies the "first response" SLA target.
    if user["role"] in STAFF_ROLES and not t["first_response_at"]:
        cur.execute("UPDATE tickets SET updated_at=NOW(), first_response_at=NOW() WHERE id=%s", (ticket_id,))
        log_action(cur, ticket_id, uid, "first_response", "")
    else:
        cur.execute("UPDATE tickets SET updated_at=NOW() WHERE id=%s", (ticket_id,))

    # Notify other party
    notify_uid = t["created_by"] if uid != t["created_by"] else (t["assigned_to"] or None)
    if notify_uid and notify_uid != uid:
        cur.execute(
            "INSERT INTO notifications (user_id, message, link) VALUES (%s,%s,%s)",
            (notify_uid, f"New reply on ticket {t['ticket_no']}", f"/ticket/{ticket_id}")
        )

    db.commit()
    cur.execute(
        "SELECT c.*, u.name as author_name, u.role as author_role FROM comments c JOIN users u ON c.user_id=u.id WHERE c.id=%s",
        (comment_id,)
    )
    comment = cur.fetchone()
    cur.close()
    db.close()
    return jsonify(dict(comment)), 201

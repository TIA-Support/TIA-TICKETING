"""Dashboard statistics"""

from flask import jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from database import get_db
from flask import Blueprint

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("", methods=["GET"])
@jwt_required()
def stats():
    uid = int(get_jwt_identity())
    db  = get_db()
    cur = db.cursor()
    cur.execute("SELECT role FROM users WHERE id=%s", (uid,))
    user = cur.fetchone()

    base = "SELECT * FROM tickets"
    if user["role"] == "client":
        cur.execute(base + " WHERE created_by=%s", (uid,))
    else:
        cur.execute(base)
    rows = cur.fetchall()

    tickets = [dict(r) for r in rows]

    by_status   = {}
    by_priority = {}
    by_category = {}
    for t in tickets:
        by_status[t["status"]]     = by_status.get(t["status"],    0) + 1
        by_priority[t["priority"]] = by_priority.get(t["priority"],0) + 1
        by_category[t["category"]] = by_category.get(t["category"],0) + 1

    # Recent 5 tickets
    cur.execute(
        """SELECT t.id, t.ticket_no, t.title, t.status, t.priority, t.created_at,
                  u.name as creator_name
           FROM tickets t JOIN users u ON t.created_by=u.id
           {}
           ORDER BY t.created_at DESC LIMIT 5""".format(
               "WHERE t.created_by=%s" if user["role"] == "client"
               else ""
           ),
        (uid,) if user["role"] == "client" else ()
    )
    recent = cur.fetchall()

    if user["role"] == "admin":
        cur.execute("SELECT COUNT(*) as c FROM users")
        total_users = cur.fetchone()["c"]
    else:
        total_users = None

    cur.close()
    db.close()
    return jsonify(
        total=len(tickets),
        by_status=by_status,
        by_priority=by_priority,
        by_category=by_category,
        recent=[dict(r) for r in recent],
        total_users=total_users,
    )

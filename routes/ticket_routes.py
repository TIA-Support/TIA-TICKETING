"""Ticket routes — CRUD + assignment + status change + SLA + audit log"""

import os
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from database import get_db, next_ticket_no, compute_sla_due_dates, sla_status, log_action, format_sast
from mailer import send_email, render_ack_email, render_resolved_email, render_assignment_email, render_new_ticket_alert_email

ticket_bp = Blueprint("tickets", __name__)

APP_BASE_URL = os.environ.get("APP_BASE_URL", "")  # e.g. https://tia-ticketing-1.onrender.com

CATEGORIES     = {"cloud", "network_security", "voip", "it_support", "hardware", "general"}
PRIORITIES     = {"low", "medium", "high", "critical"}
STATUSES       = {"open", "in_progress", "pending", "resolved", "closed"}
REQ_LEVELS     = {"Level 1", "Level 2", "Level 3", "Level 4", "Level 5"}
SUPPORT_TYPES  = {"remote", "onsite", "remote_onsite"}
STAFF_ROLES    = ("admin", "agent", "technician")


def _with_sla(row):
    d = dict(row)
    d.update(sla_status(d))
    return d


@ticket_bp.route("", methods=["GET"])
@jwt_required()
def list_tickets():
    uid  = int(get_jwt_identity())
    db   = get_db()
    cur  = db.cursor()
    cur.execute("SELECT role FROM users WHERE id=%s", (uid,))
    user = cur.fetchone()

    status   = request.args.get("status",   "")
    priority = request.args.get("priority", "")
    category = request.args.get("category", "")
    search   = request.args.get("q",        "")
    sla      = request.args.get("sla",      "")  # 'breached' to filter
    page     = max(1, int(request.args.get("page", 1)))
    per_page = 20

    base_q = """
        SELECT t.*, 
               u1.name as creator_name, u1.email as creator_email,
               u2.name as assignee_name
        FROM tickets t
        JOIN users u1 ON t.created_by = u1.id
        LEFT JOIN users u2 ON t.assigned_to = u2.id
        WHERE 1=1
    """
    params = []

    if user["role"] == "client":
        base_q += " AND t.created_by = %s"
        params.append(uid)

    if status   and status   in STATUSES:   base_q += " AND t.status = %s";   params.append(status)
    if priority and priority in PRIORITIES: base_q += " AND t.priority = %s"; params.append(priority)
    if category and category in CATEGORIES: base_q += " AND t.category = %s"; params.append(category)
    if search:
        base_q += " AND (t.title ILIKE %s OR t.ticket_no ILIKE %s OR t.description ILIKE %s)"
        s = f"%{search}%"
        params += [s, s, s]

    cur.execute(f"SELECT COUNT(*) as c FROM ({base_q}) AS sub", params)
    total = cur.fetchone()["c"]
    base_q += " ORDER BY t.created_at DESC LIMIT %s OFFSET %s"
    params += [per_page, (page - 1) * per_page]

    cur.execute(base_q, params)
    rows = cur.fetchall()
    cur.close()
    db.close()

    tickets = [_with_sla(r) for r in rows]
    if sla == "breached":
        tickets = [t for t in tickets if t["sla_response_breached"] or t["sla_resolution_breached"]]

    return jsonify(
        tickets=tickets,
        total=total, page=page, per_page=per_page,
        pages=(total + per_page - 1) // per_page
    )


@ticket_bp.route("", methods=["POST"])
@jwt_required()
def create_ticket():
    uid  = int(get_jwt_identity())
    data = request.get_json(silent=True) or {}

    db  = get_db()
    cur = db.cursor()
    cur.execute("SELECT role FROM users WHERE id=%s", (uid,))
    requester = cur.fetchone()

    # Staff can log a ticket on behalf of a client contact (e.g. logged from a phone call/email)
    created_by = uid
    on_behalf_of = data.get("on_behalf_of")
    if on_behalf_of and requester["role"] in STAFF_ROLES:
        cur.execute("SELECT id, role FROM users WHERE id=%s", (on_behalf_of,))
        target = cur.fetchone()
        if not target or target["role"] != "client":
            cur.close(); db.close()
            return jsonify(error="on_behalf_of must be an existing client contact."), 400
        created_by = target["id"]

    # Staff can assign the ticket to a technician/agent/admin right at creation
    assigned_to = None
    assignee = None
    if data.get("assigned_to") and requester["role"] in STAFF_ROLES:
        cur.execute("SELECT id, name, email, role FROM users WHERE id=%s", (data["assigned_to"],))
        candidate = cur.fetchone()
        if not candidate or candidate["role"] not in STAFF_ROLES:
            cur.close(); db.close()
            return jsonify(error="assigned_to must be an existing agent, technician, or admin."), 400
        assigned_to = candidate["id"]
        assignee = candidate

    title         = (data.get("title")         or "").strip()
    description   = (data.get("description")   or "").strip()
    category      = (data.get("category")      or "general").strip().lower()
    priority      = (data.get("priority")      or "medium").strip().lower()
    request_level = (data.get("request_level") or "Level 1").strip()
    support_type  = (data.get("support_type")  or "remote").strip().lower()

    if not title or not description:
        cur.close(); db.close(); return jsonify(error="Title and description are required."), 400
    if category      not in CATEGORIES:    category      = "general"
    if priority      not in PRIORITIES:    priority      = "medium"
    if request_level not in REQ_LEVELS:    request_level = "Level 1"
    if support_type  not in SUPPORT_TYPES: support_type  = "remote"

    now_utc = datetime.now(timezone.utc)
    response_due, resolution_due = compute_sla_due_dates(request_level, now_utc)

    ticket_no = next_ticket_no()
    cur.execute(
        """INSERT INTO tickets
               (ticket_no, title, description, category, priority,
                request_level, support_type, status, created_by, assigned_to,
                created_at, sla_response_due, sla_resolution_due)
           VALUES (%s,%s,%s,%s,%s,%s,%s,'open',%s,%s,%s,%s,%s)
           RETURNING id""",
        (ticket_no, title, description, category, priority,
         request_level, support_type, created_by, assigned_to,
         now_utc, response_due, resolution_due)
    )
    ticket_id = cur.fetchone()["id"]
    log_note = f"Priority: {priority}, Category: {category}"
    if created_by != uid:
        log_note += f" (logged on behalf of contact #{created_by})"
    if assigned_to:
        log_note += f", assigned to #{assigned_to} at creation"
    log_action(cur, ticket_id, uid, "created", log_note)
    db.commit()

    # Notify all admins/agents/technicians of the new ticket
    cur.execute("SELECT id FROM users WHERE role IN ('admin','agent','technician')")
    agents = cur.fetchall()
    for a in agents:
        cur.execute(
            "INSERT INTO notifications (user_id, message, link) VALUES (%s,%s,%s)",
            (a["id"], f"New ticket {ticket_no}: {title}", f"/ticket/{ticket_id}")
        )
    db.commit()

    # If assigned at creation and it's not a self-assignment, send the assignment email/notification too
    if assignee and assigned_to != uid:
        cur.execute(
            "INSERT INTO notifications (user_id, message, link) VALUES (%s,%s,%s)",
            (assigned_to, f"You have been assigned ticket {ticket_no}", f"/ticket/{ticket_id}")
        )
        db.commit()
        if assignee["email"]:
            ticket_link = f"{APP_BASE_URL}/ticket/{ticket_id}" if APP_BASE_URL else None
            assign_html = render_assignment_email(
                technician_name=assignee["name"],
                ticket_no=ticket_no,
                title=title,
                request_level=request_level,
                priority=priority,
                ticket_link=ticket_link,
            )
            send_email(assignee["email"], assignee["name"], f"Ticket Assigned – {ticket_no}", assign_html)

    cur.execute(
        """SELECT t.*, u1.name as creator_name, u1.email as creator_email, u1.role as creator_role,
                  u2.name as assignee_name, u2.email as assignee_email
           FROM tickets t JOIN users u1 ON t.created_by=u1.id
           LEFT JOIN users u2 ON t.assigned_to=u2.id
           WHERE t.id=%s""", (ticket_id,)
    )
    ticket = cur.fetchone()
    cur.close()
    db.close()

    if ticket["creator_role"] == "client":
        html_body = render_ack_email(
            client_name=ticket["creator_name"],
            ticket_no=ticket["ticket_no"],
            title=ticket["title"],
            date_received=format_sast(ticket["created_at"]),
            technician_name=ticket["assignee_name"],
        )
        send_email(
            ticket["creator_email"], ticket["creator_name"],
            f"Acknowledgement of Your Request – {ticket['creator_name']}",
            html_body,
            cc_email=ticket["assignee_email"],
            cc_name=ticket["assignee_name"],
        )

        # Client-opened ticket with no assignee yet — alert technicians so it doesn't sit unnoticed.
        if not ticket["assigned_to"]:
            db2  = get_db()
            cur2 = db2.cursor()
            cur2.execute("SELECT id, name, email FROM users WHERE role = 'technician'")
            staff = cur2.fetchall()
            cur2.close()
            db2.close()

            ticket_link = f"{APP_BASE_URL}/ticket/{ticket_id}" if APP_BASE_URL else None
            for s in staff:
                if not s["email"]:
                    continue
                alert_html = render_new_ticket_alert_email(
                    staff_name=s["name"],
                    ticket_no=ticket["ticket_no"],
                    title=ticket["title"],
                    request_level=ticket["request_level"],
                    client_name=ticket["creator_name"],
                    ticket_link=ticket_link,
                )
                send_email(s["email"], s["name"], f"New Ticket Needs Assignment – {ticket['ticket_no']}", alert_html)

    return jsonify(_with_sla(ticket)), 201


@ticket_bp.route("/<int:ticket_id>", methods=["GET"])
@jwt_required()
def get_ticket(ticket_id):
    uid = int(get_jwt_identity())
    db  = get_db()
    cur = db.cursor()
    cur.execute("SELECT role FROM users WHERE id=%s", (uid,))
    user = cur.fetchone()

    cur.execute(
        """SELECT t.*,
                  u1.name  as creator_name,  u1.email as creator_email,
                  u1.company as creator_company, u1.phone as creator_phone,
                  u2.name  as assignee_name, u2.email as assignee_email
           FROM tickets t
           JOIN users u1 ON t.created_by = u1.id
           LEFT JOIN users u2 ON t.assigned_to = u2.id
           WHERE t.id = %s""", (ticket_id,)
    )
    ticket = cur.fetchone()

    if not ticket:
        cur.close(); db.close(); return jsonify(error="Ticket not found."), 404
    if user["role"] == "client" and ticket["created_by"] != uid:
        cur.close(); db.close(); return jsonify(error="Access denied."), 403

    cur.execute(
        """SELECT c.*, u.name as author_name, u.role as author_role
           FROM comments c JOIN users u ON c.user_id = u.id
           WHERE c.ticket_id = %s
           ORDER BY c.created_at ASC""", (ticket_id,)
    )
    comments = cur.fetchall()

    # Mark notifications read
    cur.execute(
        "UPDATE notifications SET is_read=1 WHERE user_id=%s AND link=%s",
        (uid, f"/ticket/{ticket_id}")
    )
    db.commit()
    cur.close()
    db.close()

    result = _with_sla(ticket)
    result["comments"] = [dict(c) for c in comments
                          if not c["is_internal"] or user["role"] in STAFF_ROLES]
    return jsonify(result)


@ticket_bp.route("/<int:ticket_id>/audit", methods=["GET"])
@jwt_required()
def get_audit_log(ticket_id):
    uid = int(get_jwt_identity())
    db  = get_db()
    cur = db.cursor()
    cur.execute("SELECT role FROM users WHERE id=%s", (uid,))
    user = cur.fetchone()
    if user["role"] not in STAFF_ROLES:
        cur.close(); db.close(); return jsonify(error="Access denied."), 403

    cur.execute(
        """SELECT a.*, u.name as user_name
           FROM audit_log a LEFT JOIN users u ON a.user_id = u.id
           WHERE a.ticket_id = %s
           ORDER BY a.created_at ASC""", (ticket_id,)
    )
    rows = cur.fetchall()
    cur.close()
    db.close()
    return jsonify(audit_log=[dict(r) for r in rows])


@ticket_bp.route("/<int:ticket_id>", methods=["PUT"])
@jwt_required()
def update_ticket(ticket_id):
    uid  = int(get_jwt_identity())
    data = request.get_json(silent=True) or {}
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

    fields, params = [], []
    audit_notes = []
    just_resolved = False

    # Clients can only update title/description on open tickets
    if user["role"] == "client":
        if t["status"] not in ("open", "pending"):
            cur.close(); db.close(); return jsonify(error="Cannot edit a ticket that is in progress or closed."), 403
        for f in ("title", "description"):
            if f in data:
                fields.append(f"{f} = %s"); params.append(data[f])
    else:
        for f in ("title", "description", "priority", "category",
                  "work_implemented", "start_time", "end_time",
                  "hours_worked", "invoice_no"):
            if f in data:
                fields.append(f"{f} = %s"); params.append(data[f])
        if "request_level" in data and data["request_level"] in REQ_LEVELS:
            fields.append("request_level = %s"); params.append(data["request_level"])
        if "support_type" in data and data["support_type"] in SUPPORT_TYPES:
            fields.append("support_type = %s"); params.append(data["support_type"])

        if "status" in data and data["status"] in STATUSES:
            fields.append("status = %s"); params.append(data["status"])
            audit_notes.append(f"status: {t['status']} → {data['status']}")
            just_resolved = data["status"] in ("resolved", "closed") and not t["resolved_at"]
            if just_resolved:
                fields.append("resolved_at = NOW()")

        if "assigned_to" in data and user["role"] in STAFF_ROLES:
            new_assignee = data["assigned_to"]
            if new_assignee:
                cur.execute("SELECT id, role FROM users WHERE id=%s", (new_assignee,))
                target = cur.fetchone()
                if not target or target["role"] not in STAFF_ROLES:
                    cur.close(); db.close()
                    return jsonify(error="Tickets can only be assigned to agents, technicians, or admins."), 400
            fields.append("assigned_to = %s"); params.append(new_assignee)
            audit_notes.append(f"assigned_to: {t['assigned_to']} → {new_assignee}")

    if fields:
        fields.append("updated_at = NOW()")
        params.append(ticket_id)
        cur.execute(f"UPDATE tickets SET {', '.join(fields)} WHERE id=%s", params)
        if audit_notes:
            log_action(cur, ticket_id, uid, "updated", "; ".join(audit_notes))
        db.commit()

    # Notify ticket creator if status changed
    if "status" in data and t["created_by"] != uid:
        cur.execute(
            "INSERT INTO notifications (user_id, message, link) VALUES (%s,%s,%s)",
            (t["created_by"], f"Ticket {t['ticket_no']} status changed to {data['status']}", f"/ticket/{ticket_id}")
        )
        db.commit()

    # Notify new assignee if assignment changed
    if "assigned_to" in data and data["assigned_to"] and data["assigned_to"] != t["assigned_to"] and data["assigned_to"] != uid:
        cur.execute(
            "INSERT INTO notifications (user_id, message, link) VALUES (%s,%s,%s)",
            (data["assigned_to"], f"You have been assigned ticket {t['ticket_no']}", f"/ticket/{ticket_id}")
        )
        db.commit()

        cur.execute("SELECT name, email FROM users WHERE id=%s", (data["assigned_to"],))
        assignee = cur.fetchone()
        if assignee and assignee["email"]:
            ticket_link = f"{APP_BASE_URL}/ticket/{ticket_id}" if APP_BASE_URL else None
            html_body = render_assignment_email(
                technician_name=assignee["name"],
                ticket_no=t["ticket_no"],
                title=data.get("title", t["title"]),
                request_level=data.get("request_level", t["request_level"]),
                priority=data.get("priority", t["priority"]),
                ticket_link=ticket_link,
            )
            send_email(assignee["email"], assignee["name"], f"Ticket Assigned – {t['ticket_no']}", html_body)

    cur.execute(
        """SELECT t.*, u1.name as creator_name, u1.email as creator_email, u1.role as creator_role,
                  u2.name as assignee_name, u2.email as assignee_email
           FROM tickets t JOIN users u1 ON t.created_by=u1.id
           LEFT JOIN users u2 ON t.assigned_to=u2.id
           WHERE t.id=%s""", (ticket_id,)
    )
    updated = cur.fetchone()
    cur.close()
    db.close()

    if just_resolved and updated["creator_role"] == "client":
        html_body = render_resolved_email(
            client_name=updated["creator_name"],
            ticket_no=updated["ticket_no"],
            title=updated["title"],
            date_logged=format_sast(updated["created_at"]),
            date_resolved=format_sast(updated["resolved_at"]),
            technician_name=updated["assignee_name"],
        )
        send_email(
            updated["creator_email"], updated["creator_name"],
            f"Your Request Has Been Resolved – {updated['ticket_no']}",
            html_body,
            cc_email=updated["assignee_email"],
            cc_name=updated["assignee_name"],
        )

    return jsonify(_with_sla(updated))


@ticket_bp.route("/<int:ticket_id>", methods=["DELETE"])
@jwt_required()
def delete_ticket(ticket_id):
    uid  = int(get_jwt_identity())
    db   = get_db()
    cur  = db.cursor()
    cur.execute("SELECT role FROM users WHERE id=%s", (uid,))
    user = cur.fetchone()
    if user["role"] not in ("admin",):
        cur.close(); db.close(); return jsonify(error="Admins only."), 403
    cur.execute("DELETE FROM tickets WHERE id=%s", (ticket_id,))
    db.commit()
    cur.close()
    db.close()
    return jsonify(message="Ticket deleted.")

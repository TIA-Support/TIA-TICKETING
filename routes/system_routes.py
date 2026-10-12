"""System/automation routes — SLA reminder job + one-time backfills, triggered manually or by a scheduler."""

import os
from datetime import timezone
from flask import Blueprint, request, jsonify
from database import get_db, sla_status, log_action, compute_sla_due_dates
from mailer import send_email, render_sla_reminder_email

system_bp = Blueprint("system", __name__)

CRON_SECRET = os.environ.get("CRON_SECRET")
APP_BASE_URL = os.environ.get("APP_BASE_URL", "")  # e.g. https://tia-ticketing-1.onrender.com — used to build links in emails
REMINDER_COOLDOWN_HOURS = 4  # don't re-notify the same ticket more than once per this window
OPEN_STATUSES = ("open", "in_progress", "pending")
STAFF_ROLES = ("admin", "agent", "technician")


def _check_secret():
    """Accept the secret via Authorization header OR a ?secret= query param,
    so this can be triggered from a plain browser visit, not just curl/Postman."""
    auth = request.headers.get("Authorization", "")
    header_secret = auth[7:] if auth.startswith("Bearer ") else None
    provided = header_secret or request.args.get("secret")
    return bool(CRON_SECRET) and provided == CRON_SECRET


@system_bp.route("/check-sla", methods=["POST"])
def check_sla():
    if not _check_secret():
        return jsonify(error="Unauthorized."), 401

    db  = get_db()
    cur = db.cursor()
    cur.execute(
        f"""SELECT * FROM tickets
            WHERE status IN ({','.join(['%s']*len(OPEN_STATUSES))})""",
        OPEN_STATUSES
    )
    tickets = cur.fetchall()

    notified = []
    for t in tickets:
        status = sla_status(t)
        if not (status["sla_response_breached"] or status["sla_resolution_breached"]):
            continue

        # Throttle: skip if we already reminded within the cooldown window
        if t["last_sla_reminder_at"]:
            cur.execute(
                "SELECT NOW() - %s < (%s || ' hours')::interval as recent",
                (t["last_sla_reminder_at"], REMINDER_COOLDOWN_HOURS)
            )
            if cur.fetchone()["recent"]:
                continue

        breach_kind = []
        if status["sla_response_breached"]:   breach_kind.append("first response")
        if status["sla_resolution_breached"]: breach_kind.append("resolution")
        breach_text = " and ".join(breach_kind)

        message = f"SLA breach ({breach_text}) on {t['ticket_no']}: {t['title']}"

        # Recipients: assigned technician, or every staff member if unassigned
        if t["assigned_to"]:
            cur.execute("SELECT id, name, email FROM users WHERE id=%s", (t["assigned_to"],))
            recipients = cur.fetchall()
        else:
            cur.execute("SELECT id, name, email FROM users WHERE role IN ('admin','agent','technician')")
            recipients = cur.fetchall()

        ticket_link = f"{APP_BASE_URL}/ticket/{t['id']}" if APP_BASE_URL else None

        for r in recipients:
            cur.execute(
                "INSERT INTO notifications (user_id, message, link) VALUES (%s,%s,%s)",
                (r["id"], message, f"/ticket/{t['id']}")
            )
            if r["email"]:
                html_body = render_sla_reminder_email(
                    technician_name=r["name"],
                    ticket_no=t["ticket_no"],
                    title=t["title"],
                    request_level=t["request_level"],
                    breach_text=breach_text,
                    ticket_link=ticket_link,
                )
                send_email(r["email"], r["name"], f"SLA Breach Alert – {t['ticket_no']}", html_body)

        log_action(cur, t["id"], None, "sla_reminder", breach_text)
        cur.execute("UPDATE tickets SET last_sla_reminder_at = NOW() WHERE id=%s", (t["id"],))
        notified.append(t["ticket_no"])

    db.commit()
    cur.close()
    db.close()
    return jsonify(checked=len(tickets), reminders_sent=len(notified), tickets=notified)


@system_bp.route("/backfill-sla-dates", methods=["GET", "POST"])
def backfill_sla_dates():
    """One-time fixup: recompute sla_response_due/sla_resolution_due for every
    still-open ticket using the business-hours-aware calculator, based on each
    ticket's actual created_at and request_level. Resolved/closed tickets are
    left untouched, since their historical breach outcome already happened and
    shouldn't be rewritten after the fact. Safe to run more than once."""
    if not _check_secret():
        return jsonify(error="Unauthorized."), 401

    db  = get_db()
    cur = db.cursor()
    cur.execute(
        f"""SELECT id, created_at, request_level FROM tickets
            WHERE status IN ({','.join(['%s']*len(OPEN_STATUSES))})""",
        OPEN_STATUSES
    )
    tickets = cur.fetchall()

    updated = []
    for t in tickets:
        created_at = t["created_at"]
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        response_due, resolution_due = compute_sla_due_dates(t["request_level"], created_at)
        cur.execute(
            "UPDATE tickets SET sla_response_due=%s, sla_resolution_due=%s WHERE id=%s",
            (response_due, resolution_due, t["id"])
        )
        updated.append(t["id"])

    db.commit()
    cur.close()
    db.close()
    return jsonify(updated_count=len(updated), ticket_ids=updated)


@system_bp.route("/backfill-first-response", methods=["GET", "POST"])
def backfill_first_response():
    """One-time fixup: for every ticket where first_response_at is still NULL,
    find the earliest staff (admin/agent/technician) comment on that ticket and
    backdate first_response_at to when that reply actually happened. Tickets
    with no staff reply yet are left untouched. Safe to run more than once —
    it only ever fills in NULLs, never overwrites an existing value."""
    if not _check_secret():
        return jsonify(error="Unauthorized."), 401

    db  = get_db()
    cur = db.cursor()
    cur.execute(
        """UPDATE tickets t
           SET first_response_at = sub.first_staff_reply
           FROM (
               SELECT c.ticket_id, MIN(c.created_at) as first_staff_reply
               FROM comments c
               JOIN users u ON c.user_id = u.id
               WHERE u.role IN %s
               GROUP BY c.ticket_id
           ) sub
           WHERE t.id = sub.ticket_id AND t.first_response_at IS NULL
           RETURNING t.id, t.ticket_no, t.first_response_at""",
        (STAFF_ROLES,)
    )
    updated = cur.fetchall()

    for row in updated:
        log_action(cur, row["id"], None, "first_response_backfilled", str(row["first_response_at"]))

    db.commit()
    cur.close()
    db.close()
    return jsonify(
        updated_count=len(updated),
        tickets=[{"id": r["id"], "ticket_no": r["ticket_no"], "first_response_at": str(r["first_response_at"])} for r in updated]
    )


@system_bp.route("/backfill-client-companies", methods=["GET", "POST"])
def backfill_client_companies():
    """One-time fixup: link every client-role user who has no client_id yet
    (e.g. anyone who self-registered before registration required/linked a
    company) to a clients row, so they actually show up under the Clients tab.
    Uses each user's existing `company` text field to find-or-create a
    matching company; users with no company text on file are grouped under
    a single "Unassigned Clients" company so nobody is left invisible. Safe
    to run more than once — only ever fills in NULL client_id, never
    reassigns an existing one."""
    if not _check_secret():
        return jsonify(error="Unauthorized."), 401

    db  = get_db()
    cur = db.cursor()
    cur.execute("SELECT id, company FROM users WHERE role='client' AND client_id IS NULL")
    orphans = cur.fetchall()

    updated = []
    for u in orphans:
        company_name = (u["company"] or "").strip() or "Unassigned Clients"
        cur.execute("SELECT id FROM clients WHERE LOWER(name) = LOWER(%s)", (company_name,))
        existing = cur.fetchone()
        if existing:
            client_id = existing["id"]
        else:
            cur.execute("INSERT INTO clients (name) VALUES (%s) RETURNING id", (company_name,))
            client_id = cur.fetchone()["id"]

        cur.execute("UPDATE users SET client_id=%s WHERE id=%s", (client_id, u["id"]))
        updated.append({"user_id": u["id"], "company": company_name})

    db.commit()
    cur.close()
    db.close()
    return jsonify(updated_count=len(updated), users=updated)

"""
TIA-Solutions Ticketing System
Database initialisation and helper utilities (MySQL / MariaDB).
"""

import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, unquote
from zoneinfo import ZoneInfo

import pymysql
import pymysql.cursors

# Format: mysql+pymysql://DBUSER:DBPASSWORD@localhost/DBNAME
DATABASE_URL = os.environ.get("DATABASE_URL")

# TIA-Solutions SLA framework — targets in business hours, keyed by request level (1-5).
# The SLA clock only runs Mon-Fri 08:00-16:00 SAST; it's paused nights/weekends,
# so a ticket logged after hours simply starts counting at the next business open.
SLA_TARGETS = {
    5: {"response": 2,  "resolution": 4},   # Very High
    4: {"response": 4,  "resolution": 8},   # High
    3: {"response": 4,  "resolution": 48},  # Medium/Normal
    2: {"response": 8,  "resolution": 24},  # Low
    1: {"response": 12, "resolution": 48},  # Request
}

OFFICE_TZ_NAME    = "Africa/Johannesburg"
OFFICE_DAYS       = {0, 1, 2, 3, 4}  # Monday=0 .. Friday=4
OFFICE_START_HOUR = 8
OFFICE_END_HOUR   = 16


def is_office_hours(dt=None):
    """True if the given (or current) moment falls within Mon-Fri 08:00-16:00 SAST."""
    tz = ZoneInfo(OFFICE_TZ_NAME)
    if dt is None:
        local = datetime.now(tz)
    else:
        local = dt.astimezone(tz) if dt.tzinfo else dt.replace(tzinfo=timezone.utc).astimezone(tz)
    return local.weekday() in OFFICE_DAYS and OFFICE_START_HOUR <= local.hour < OFFICE_END_HOUR


def _next_business_start(local_dt):
    """Given a tz-aware local datetime, return the next moment inside business
    hours — itself, if already inside; otherwise the next 08:00 on a business day."""
    while True:
        if local_dt.weekday() in OFFICE_DAYS and OFFICE_START_HOUR <= local_dt.hour < OFFICE_END_HOUR:
            return local_dt
        if local_dt.weekday() in OFFICE_DAYS and local_dt.hour < OFFICE_START_HOUR:
            return local_dt.replace(hour=OFFICE_START_HOUR, minute=0, second=0, microsecond=0)
        # After hours on a business day, or a weekend day — roll to the start of the next day and retry.
        local_dt = (local_dt + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def add_business_hours(start_utc, hours):
    """Add `hours` of business time (Mon-Fri 08:00-16:00 SAST) to start_utc.
    Nights and weekends are skipped entirely — the clock only runs during
    business hours. Returns an aware UTC datetime."""
    tz = ZoneInfo(OFFICE_TZ_NAME)
    remaining = timedelta(hours=hours)
    current = _next_business_start(start_utc.astimezone(tz))

    while remaining > timedelta(0):
        day_end = current.replace(hour=OFFICE_END_HOUR, minute=0, second=0, microsecond=0)
        available_today = day_end - current
        if remaining <= available_today:
            current = current + remaining
            remaining = timedelta(0)
        else:
            remaining -= available_today
            current = _next_business_start(day_end + timedelta(minutes=1))

    return current.astimezone(timezone.utc)


def compute_sla_due_dates(request_level, created_at_utc):
    """Return (response_due_utc, resolution_due_utc) — both business-hours-aware,
    aware UTC datetimes — based on the TIA SLA framework."""
    try:
        level_num = int(str(request_level).strip().split()[-1])
    except (ValueError, IndexError):
        level_num = 3
    targets = SLA_TARGETS.get(level_num, SLA_TARGETS[3])
    response_due   = add_business_hours(created_at_utc, targets["response"])
    resolution_due = add_business_hours(created_at_utc, targets["resolution"])
    return response_due, resolution_due


def get_db():
    """Return a live connection whose cursors give dict rows.

    The session time zone is forced to UTC so NOW() / CURRENT_TIMESTAMP store
    UTC, which is what the rest of the app (SLA maths, format_sast) expects.
    """
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is not set. Set it to "
            "mysql+pymysql://DBUSER:DBPASSWORD@localhost/DBNAME"
        )
    u = urlparse(DATABASE_URL)
    return pymysql.connect(
        host=u.hostname or "localhost",
        port=u.port or 3306,
        user=unquote(u.username or ""),
        password=unquote(u.password or ""),
        database=(u.path or "/").lstrip("/"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        init_command="SET time_zone = '+00:00'",
        autocommit=False,
    )


_TABLE_OPTS = "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"


def init_db():
    conn = get_db()
    c = conn.cursor()
    try:
        # Clients (companies) — a client's contacts are users with role='client' and client_id set
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS clients (
                id         INT AUTO_INCREMENT PRIMARY KEY,
                name       VARCHAR(190) NOT NULL,
                notes      TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_clients_name (name)
            ) {_TABLE_OPTS}
        """)

        # Users
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS users (
                id                  INT AUTO_INCREMENT PRIMARY KEY,
                name                VARCHAR(255) NOT NULL,
                email               VARCHAR(190) NOT NULL,
                password            VARCHAR(255) NOT NULL,
                role                VARCHAR(30)  NOT NULL DEFAULT 'client',
                company             VARCHAR(255),
                phone               VARCHAR(60),
                client_id           INT NULL,
                reset_token         VARCHAR(255),
                reset_token_expires DATETIME NULL,
                created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_users_email (email),
                CONSTRAINT fk_users_client FOREIGN KEY (client_id) REFERENCES clients(id)
            ) {_TABLE_OPTS}
        """)

        # Tickets
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS tickets (
                id                   INT AUTO_INCREMENT PRIMARY KEY,
                ticket_no            VARCHAR(40)  NOT NULL,
                title                TEXT         NOT NULL,
                description          TEXT         NOT NULL,
                category             VARCHAR(40)  NOT NULL,
                priority             VARCHAR(20)  NOT NULL DEFAULT 'medium',
                request_level        VARCHAR(20)  NOT NULL DEFAULT 'Level 1',
                status               VARCHAR(20)  NOT NULL DEFAULT 'open',
                support_type         VARCHAR(20)  DEFAULT 'remote',
                work_implemented     TEXT,
                start_time           TEXT,
                end_time             TEXT,
                hours_worked         TEXT,
                invoice_no           TEXT,
                created_by           INT NOT NULL,
                assigned_to          INT NULL,
                created_at           DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at           DATETIME DEFAULT CURRENT_TIMESTAMP,
                sla_response_due     DATETIME NULL,
                sla_resolution_due   DATETIME NULL,
                first_response_at    DATETIME NULL,
                resolved_at          DATETIME NULL,
                last_sla_reminder_at DATETIME NULL,
                UNIQUE KEY uq_tickets_no (ticket_no),
                CONSTRAINT fk_tickets_creator  FOREIGN KEY (created_by)  REFERENCES users(id),
                CONSTRAINT fk_tickets_assignee FOREIGN KEY (assigned_to) REFERENCES users(id)
            ) {_TABLE_OPTS}
        """)

        # Comments
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS comments (
                id          INT AUTO_INCREMENT PRIMARY KEY,
                ticket_id   INT NOT NULL,
                user_id     INT NOT NULL,
                body        TEXT NOT NULL,
                is_internal INT DEFAULT 0,
                created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_comments_ticket FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE,
                CONSTRAINT fk_comments_user   FOREIGN KEY (user_id)   REFERENCES users(id)
            ) {_TABLE_OPTS}
        """)

        # Notifications
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS notifications (
                id         INT AUTO_INCREMENT PRIMARY KEY,
                user_id    INT NOT NULL,
                message    TEXT NOT NULL,
                link       VARCHAR(500),
                is_read    INT DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_notifications_user FOREIGN KEY (user_id) REFERENCES users(id)
            ) {_TABLE_OPTS}
        """)

        # Job cards — standalone onsite work records, optionally linked to a ticket
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS job_cards (
                id                   INT AUTO_INCREMENT PRIMARY KEY,
                job_card_no          VARCHAR(40) NOT NULL,
                ticket_id            INT NULL,
                customer_name        TEXT,
                address              TEXT,
                contact_name         TEXT,
                tel_no               TEXT,
                email                TEXT,
                date_received        DATETIME NULL,
                instruction_taken_by TEXT,
                job_done_by          TEXT,
                time_started         TEXT,
                time_completed       TEXT,
                instructions         TEXT,
                comments             TEXT,
                signed_by            TEXT,
                designation          TEXT,
                signed_date          DATETIME NULL,
                created_by           INT NULL,
                created_at           DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at           DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_job_cards_no (job_card_no),
                CONSTRAINT fk_job_cards_ticket  FOREIGN KEY (ticket_id)  REFERENCES tickets(id) ON DELETE SET NULL,
                CONSTRAINT fk_job_cards_creator FOREIGN KEY (created_by) REFERENCES users(id)
            ) {_TABLE_OPTS}
        """)

        # Audit log
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS audit_log (
                id         INT AUTO_INCREMENT PRIMARY KEY,
                ticket_id  INT NULL,
                user_id    INT NULL,
                action     TEXT NOT NULL,
                details    TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_audit_ticket FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE,
                CONSTRAINT fk_audit_user   FOREIGN KEY (user_id)   REFERENCES users(id)
            ) {_TABLE_OPTS}
        """)

        # Counters — replaces the Postgres sequences used for ticket / job card numbers
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS counters (
                name  VARCHAR(40) NOT NULL PRIMARY KEY,
                value INT NOT NULL DEFAULT 0
            ) {_TABLE_OPTS}
        """)
        c.execute("INSERT IGNORE INTO counters (name, value) VALUES ('ticket_no', 0), ('job_card_no', 0)")

        conn.commit()

        _seed_admin(conn, c)
    finally:
        c.close()
        conn.close()


def _seed_admin(conn, c):
    """Create the first admin account on an EMPTY users table only.

    The password comes from the ADMIN_PASSWORD environment variable (never from
    the source code). If it isn't set, nothing is created.
    Optional: ADMIN_EMAIL (defaults to admin@tia-solutions.co.za).
    """
    admin_password = os.environ.get("ADMIN_PASSWORD")
    if not admin_password:
        return
    admin_email = (os.environ.get("ADMIN_EMAIL") or "admin@tia-solutions.co.za").strip().lower()

    c.execute("SELECT COUNT(*) AS n FROM users")
    if c.fetchone()["n"] != 0:
        return

    from werkzeug.security import generate_password_hash
    try:
        c.execute(
            "INSERT INTO users (name, email, password, role, company) VALUES (%s,%s,%s,%s,%s)",
            ("TIA Admin", admin_email, generate_password_hash(admin_password), "admin", "TIA Solutions"),
        )
        conn.commit()
    except pymysql.err.IntegrityError:
        # Another worker process created it at the same moment — that's fine.
        conn.rollback()


def format_sast(dt, fmt="%d %B %Y %H:%M"):
    """Format a stored (UTC, possibly naive) datetime as South African time.
    Used anywhere a human-readable date is generated server-side (emails),
    since the database stores raw UTC and the frontend does its own SAST
    conversion in JS — this is the backend equivalent."""
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo(OFFICE_TZ_NAME)).strftime(fmt)


def _next_counter(name):
    """Atomically increment and return a named counter."""
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("UPDATE counters SET value = LAST_INSERT_ID(value + 1) WHERE name = %s", (name,))
        c.execute("SELECT LAST_INSERT_ID() AS num")
        num = int(c.fetchone()["num"])
        conn.commit()
        c.close()
        return num
    finally:
        conn.close()


def next_ticket_no():
    return f"TIA-{_next_counter('ticket_no'):05d}"


def next_job_card_no():
    return f"JC-{_next_counter('job_card_no'):05d}"


def log_action(cur, ticket_id, user_id, action, details=""):
    """Write one row to audit_log. Caller is responsible for conn.commit()."""
    cur.execute(
        "INSERT INTO audit_log (ticket_id, user_id, action, details) VALUES (%s,%s,%s,%s)",
        (ticket_id, user_id, action, details)
    )


def sla_status(ticket):
    """Given a ticket dict/row, compute SLA breach flags. Returns a dict to merge in."""
    now = datetime.now(timezone.utc)

    def _aware(dt):
        if dt is None:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    resp_due  = _aware(ticket.get("sla_response_due"))
    reso_due  = _aware(ticket.get("sla_resolution_due"))
    first_resp = _aware(ticket.get("first_response_at"))
    resolved   = _aware(ticket.get("resolved_at"))

    response_breached = bool(
        resp_due and (
            (first_resp and first_resp > resp_due) or
            (not first_resp and now > resp_due)
        )
    )
    resolution_breached = bool(
        reso_due and (
            (resolved and resolved > reso_due) or
            (not resolved and now > reso_due)
        )
    )
    return {
        "sla_response_breached":   response_breached,
        "sla_resolution_breached": resolution_breached,
    }

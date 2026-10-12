"""
TIA-Solutions Ticketing System
Database initialisation and helper utilities (PostgreSQL / Neon).
"""

import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import psycopg2
import psycopg2.extras

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
    """Return a live connection with dict-like rows (RealDictRow)."""
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is not set. Add your Neon connection string as the "
            "DATABASE_URL environment variable."
        )
    conn = psycopg2.connect(DATABASE_URL, sslmode="require",
                             cursor_factory=psycopg2.extras.RealDictCursor)
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()

    # Users
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id        SERIAL PRIMARY KEY,
            name      TEXT    NOT NULL,
            email     TEXT    NOT NULL UNIQUE,
            password  TEXT    NOT NULL,
            role      TEXT    NOT NULL DEFAULT 'client',
            company   TEXT,
            phone     TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)

    # Clients (companies) — a client's contacts are users with role='client' and client_id set
    c.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            id         SERIAL PRIMARY KEY,
            name       TEXT   NOT NULL UNIQUE,
            notes      TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS client_id INTEGER REFERENCES clients(id)")
    c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_token TEXT")
    c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_token_expires TIMESTAMP")

    # Tickets
    c.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            id                SERIAL  PRIMARY KEY,
            ticket_no         TEXT    NOT NULL UNIQUE,
            title             TEXT    NOT NULL,
            description       TEXT    NOT NULL,
            category          TEXT    NOT NULL,
            priority          TEXT    NOT NULL DEFAULT 'medium',
            request_level     TEXT    NOT NULL DEFAULT 'Level 1',
            status            TEXT    NOT NULL DEFAULT 'open',
            support_type      TEXT    DEFAULT 'remote',
            work_implemented  TEXT,
            start_time        TEXT,
            end_time          TEXT,
            hours_worked      TEXT,
            invoice_no        TEXT,
            created_by        INTEGER NOT NULL REFERENCES users(id),
            assigned_to       INTEGER REFERENCES users(id),
            created_at        TIMESTAMP DEFAULT NOW(),
            updated_at        TIMESTAMP DEFAULT NOW()
        )
    """)

    # Safe column migrations (idempotent on Postgres 9.6+)
    migrations = [
        ("request_level",     "TEXT NOT NULL DEFAULT 'Level 1'"),
        ("support_type",      "TEXT DEFAULT 'remote'"),
        ("work_implemented",  "TEXT"),
        ("start_time",        "TEXT"),
        ("end_time",          "TEXT"),
        ("hours_worked",      "TEXT"),
        ("invoice_no",        "TEXT"),
        ("sla_response_due",   "TIMESTAMP"),
        ("sla_resolution_due", "TIMESTAMP"),
        ("first_response_at",  "TIMESTAMP"),
        ("resolved_at",        "TIMESTAMP"),
        ("last_sla_reminder_at", "TIMESTAMP"),
    ]
    for col, col_def in migrations:
        c.execute(f"ALTER TABLE tickets ADD COLUMN IF NOT EXISTS {col} {col_def}")

    # Comments
    c.execute("""
        CREATE TABLE IF NOT EXISTS comments (
            id         SERIAL  PRIMARY KEY,
            ticket_id  INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
            user_id    INTEGER NOT NULL REFERENCES users(id),
            body       TEXT    NOT NULL,
            is_internal INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)

    # Notifications
    c.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id         SERIAL  PRIMARY KEY,
            user_id    INTEGER NOT NULL REFERENCES users(id),
            message    TEXT    NOT NULL,
            link       TEXT,
            is_read    INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)

    c.execute("CREATE SEQUENCE IF NOT EXISTS ticket_no_seq START 1")

    # Job cards — standalone onsite work records, optionally linked to a ticket
    c.execute("""
        CREATE TABLE IF NOT EXISTS job_cards (
            id                    SERIAL  PRIMARY KEY,
            job_card_no           TEXT    NOT NULL UNIQUE,
            ticket_id             INTEGER REFERENCES tickets(id) ON DELETE SET NULL,
            customer_name         TEXT,
            address               TEXT,
            contact_name          TEXT,
            tel_no                TEXT,
            email                 TEXT,
            date_received         TIMESTAMP,
            instruction_taken_by  TEXT,
            job_done_by           TEXT,
            time_started          TEXT,
            time_completed        TEXT,
            instructions          TEXT,
            comments              TEXT,
            signed_by             TEXT,
            designation           TEXT,
            signed_date           TIMESTAMP,
            created_by            INTEGER REFERENCES users(id),
            created_at            TIMESTAMP DEFAULT NOW(),
            updated_at            TIMESTAMP DEFAULT NOW()
        )
    """)
    c.execute("CREATE SEQUENCE IF NOT EXISTS job_card_no_seq START 1")

    # Audit log
    c.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id         SERIAL  PRIMARY KEY,
            ticket_id  INTEGER REFERENCES tickets(id) ON DELETE CASCADE,
            user_id    INTEGER REFERENCES users(id),
            action     TEXT    NOT NULL,
            details    TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)

    conn.commit()

    # Seed default admin + demo agent
    from werkzeug.security import generate_password_hash
    c.execute("SELECT id FROM users WHERE email = %s", ("admin@tia-solutions.co.za",))
    existing = c.fetchone()
    if not existing:
        c.execute(
            "INSERT INTO users (name, email, password, role, company) VALUES (%s,%s,%s,%s,%s)",
            ("TIA Admin", "admin@tia-solutions.co.za",
             generate_password_hash("Admin@1234"), "admin", "TIA Solutions")
        )
        c.execute(
            "INSERT INTO users (name, email, password, role, company) VALUES (%s,%s,%s,%s,%s)",
            ("Support Agent", "agent@tia-solutions.co.za",
             generate_password_hash("Agent@1234"), "agent", "TIA Solutions")
        )
        conn.commit()

    c.close()
    conn.close()


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


def next_ticket_no():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT nextval('ticket_no_seq') as num")
    num = c.fetchone()["num"]
    c.close()
    conn.close()
    return f"TIA-{num:05d}"


def next_job_card_no():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT nextval('job_card_no_seq') as num")
    num = c.fetchone()["num"]
    c.close()
    conn.close()
    return f"JC-{num:05d}"


def log_action(cur, ticket_id, user_id, action, details=""):
    """Write one row to audit_log. Caller is responsible for conn.commit()."""
    cur.execute(
        "INSERT INTO audit_log (ticket_id, user_id, action, details) VALUES (%s,%s,%s,%s)",
        (ticket_id, user_id, action, details)
    )


def sla_status(ticket):
    """Given a ticket dict/row, compute SLA breach flags. Returns a dict to merge in."""
    from datetime import datetime, timezone
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

"""
Outbound email — acknowledgement & resolution notices to clients.

Sends via Microsoft Graph API (app-only OAuth, client credentials flow),
using a Mail.Send application permission scoped to the support@ mailbox via
an Exchange Application Access Policy. Configured entirely through
environment variables. Until GRAPH_CLIENT_ID/GRAPH_CLIENT_SECRET/
GRAPH_TENANT_ID are set, send_email() safely no-ops (logs and returns False)
so ticket actions never fail because email isn't wired up yet.
"""

import os
import time
import html
import requests

GRAPH_TENANT_ID     = os.environ.get("GRAPH_TENANT_ID")
GRAPH_CLIENT_ID     = os.environ.get("GRAPH_CLIENT_ID")
GRAPH_CLIENT_SECRET = os.environ.get("GRAPH_CLIENT_SECRET")
GRAPH_SENDER        = os.environ.get("GRAPH_SENDER", "support@tia-solutions.co.za")
SMTP_FROM_NAME      = os.environ.get("SMTP_FROM_NAME", "TIA Solutions Support")
SUPPORT_PHONE       = "010 025 2503"

_TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
_GRAPH_SEND_URL = "https://graph.microsoft.com/v1.0/users/{sender}/sendMail"

# In-process token cache — avoids requesting a new token on every email.
# Each gunicorn worker keeps its own cache, which is fine; tokens are cheap to fetch.
_token_cache = {"access_token": None, "expires_at": 0}


def is_configured():
    return bool(GRAPH_TENANT_ID and GRAPH_CLIENT_ID and GRAPH_CLIENT_SECRET)


def _get_access_token():
    """Return a cached app-only access token, refreshing it if expired or near-expiry."""
    if _token_cache["access_token"] and time.time() < _token_cache["expires_at"] - 60:
        return _token_cache["access_token"]

    url = _TOKEN_URL.format(tenant=GRAPH_TENANT_ID)
    resp = requests.post(url, data={
        "client_id":     GRAPH_CLIENT_ID,
        "client_secret": GRAPH_CLIENT_SECRET,
        "scope":         "https://graph.microsoft.com/.default",
        "grant_type":    "client_credentials",
    }, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    _token_cache["access_token"] = data["access_token"]
    _token_cache["expires_at"]   = time.time() + int(data.get("expires_in", 3600))
    return _token_cache["access_token"]


def send_email(to_email, to_name, subject, html_body, cc_email=None, cc_name=None):
    """Send an HTML email via Graph, optionally CC'ing one address (e.g. the
    assigned technician on client-facing emails). Never raises — a failed/
    unconfigured send must never break the ticket action that triggered it."""
    if not is_configured():
        print(f"[mailer] Graph API not configured — skipping email to {to_email}: {subject}")
        return False
    try:
        token = _get_access_token()
        payload = {
            "message": {
                "subject": subject,
                "body": {"contentType": "HTML", "content": html_body},
                "toRecipients": [
                    {"emailAddress": {"address": to_email, "name": to_name or to_email}}
                ],
                "from": {"emailAddress": {"address": GRAPH_SENDER, "name": SMTP_FROM_NAME}},
            },
            "saveToSentItems": "true",
        }
        if cc_email:
            payload["message"]["ccRecipients"] = [
                {"emailAddress": {"address": cc_email, "name": cc_name or cc_email}}
            ]
        url = _GRAPH_SEND_URL.format(sender=GRAPH_SENDER)
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload,
            timeout=15,
        )
        if resp.status_code == 202:
            return True
        print(f"[mailer] Graph sendMail failed ({resp.status_code}) for {to_email}: {resp.text[:500]}")
        return False
    except Exception as e:
        print(f"[mailer] Failed to send email to {to_email}: {e}")
        return False


def _wrap(inner_html):
    return f"""<!DOCTYPE html>
<html>
<body style="font-family: Arial, sans-serif; color:#333333; line-height:1.6; max-width:600px; margin:0 auto; padding:16px;">
{inner_html}
</body>
</html>"""


def render_ack_email(client_name, ticket_no, title, date_received, technician_name=None):
    client_name = html.escape(str(client_name))
    title       = html.escape(str(title))
    technician  = html.escape(str(technician_name)) if technician_name else "To be assigned"

    inner = f"""
<p>Dear {client_name},</p>

<p>Thank you for reaching out to Technology Infrastructure Architects. This email serves as
confirmation that we have received your request regarding:</p>

<p><strong>Request:</strong> {title} (Ticket {html.escape(ticket_no)})<br>
<strong>Date Received:</strong> {html.escape(date_received)}</p>

<p>Our team is reviewing the details, and we will keep you updated on the progress. Should we
require any additional information, we will contact you directly.</p>

<p>If you have further questions or need urgent assistance, please don't hesitate to contact us
at <a href="mailto:support@tia-solutions.co.za">support@tia-solutions.co.za</a> or {SUPPORT_PHONE}.</p>

<p>We appreciate your trust in us to support your business needs.</p>

<p><strong>Request allocated to:</strong> {technician}</p>
"""
    return _wrap(inner)


def render_resolved_email(client_name, ticket_no, title, date_logged, date_resolved, technician_name=None):
    client_name = html.escape(str(client_name))
    title       = html.escape(str(title))
    technician  = html.escape(str(technician_name)) if technician_name else "TIA Support Team"

    inner = f"""
<p>Dear {client_name},</p>

<p>We are pleased to inform you that your request has been successfully resolved. Please find
the details below:</p>

<p><strong>Request:</strong> {title} (Ticket {html.escape(ticket_no)})<br>
<strong>Date Logged:</strong> {html.escape(date_logged)}<br>
<strong>Date Resolved:</strong> {html.escape(date_resolved)}</p>

<p>Our team has completed the necessary steps to address this matter, and everything is now
functioning as expected.</p>

<p>If you experience any further issues or require additional assistance, please do not
hesitate to reach out to us at <a href="mailto:support@tia-solutions.co.za">support@tia-solutions.co.za</a>
or {SUPPORT_PHONE}.</p>

<p>Thank you for giving us the opportunity to support your business. We value your partnership
with Technology Infrastructure Architects.</p>

<p><strong>Request completed by:</strong> {technician}</p>
"""
    return _wrap(inner)


def render_sla_reminder_email(technician_name, ticket_no, title, request_level, breach_text, ticket_link=None):
    technician_name = html.escape(str(technician_name))
    title           = html.escape(str(title))
    breach_text     = html.escape(str(breach_text))
    link_line       = f'<p><a href="{html.escape(ticket_link)}">View ticket {html.escape(ticket_no)}</a></p>' if ticket_link else ""

    inner = f"""
<p>Hi {technician_name},</p>

<p>This is an automated reminder that the following ticket has breached its SLA target
({breach_text}):</p>

<p><strong>Ticket:</strong> {html.escape(ticket_no)}<br>
<strong>Request:</strong> {title}<br>
<strong>Request Level:</strong> {html.escape(str(request_level))}</p>

{link_line}

<p>Please review and action this ticket as soon as possible.</p>

<p>— TIA Ticketing System</p>
"""
    return _wrap(inner)


def render_assignment_email(technician_name, ticket_no, title, request_level, priority, ticket_link=None):
    technician_name = html.escape(str(technician_name))
    title           = html.escape(str(title))
    link_line       = f'<p><a href="{html.escape(ticket_link)}">View ticket {html.escape(ticket_no)}</a></p>' if ticket_link else ""

    inner = f"""
<p>Hi {technician_name},</p>

<p>You have been assigned a new support ticket:</p>

<p><strong>Ticket:</strong> {html.escape(ticket_no)}<br>
<strong>Request:</strong> {title}<br>
<strong>Request Level:</strong> {html.escape(str(request_level))}<br>
<strong>Priority:</strong> {html.escape(str(priority))}</p>

{link_line}

<p>Please review and action this ticket at your earliest convenience.</p>

<p>— TIA Ticketing System</p>
"""
    return _wrap(inner)


def render_new_ticket_alert_email(staff_name, ticket_no, title, request_level, client_name, ticket_link=None):
    staff_name  = html.escape(str(staff_name))
    title       = html.escape(str(title))
    client_name = html.escape(str(client_name))
    link_line   = f'<p><a href="{html.escape(ticket_link)}">View ticket {html.escape(ticket_no)}</a></p>' if ticket_link else ""

    inner = f"""
<p>Hi {staff_name},</p>

<p>A new support request has come in from a client and still needs to be assigned:</p>

<p><strong>Ticket:</strong> {html.escape(ticket_no)}<br>
<strong>Request:</strong> {title}<br>
<strong>Request Level:</strong> {html.escape(str(request_level))}<br>
<strong>Logged by:</strong> {client_name}</p>

{link_line}

<p>Please assign this ticket to a technician as soon as possible.</p>

<p>— TIA Ticketing System</p>
"""
    return _wrap(inner)


def render_password_reset_email(user_name, reset_link, valid_hours=1):
    user_name = html.escape(str(user_name))

    if reset_link:
        link_block = f'<p><a href="{html.escape(reset_link)}">Reset your password</a></p>'
    else:
        # APP_BASE_URL isn't configured — degrade gracefully rather than send a dead-end email.
        link_block = "<p>Please contact TIA Solutions support to complete your password reset.</p>"

    inner = f"""
<p>Hi {user_name},</p>

<p>We received a request to reset your TIA Ticketing password. Click the link below to
choose a new one:</p>

{link_block}

<p>This link expires in {valid_hours} hour{'s' if valid_hours != 1 else ''}. If you didn't
request this, you can safely ignore this email — your password won't be changed.</p>

<p>— TIA Ticketing System</p>
"""
    return _wrap(inner)

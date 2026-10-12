"""
TIA-Solutions Ticketing System — Main Flask Application
"""

import os
from flask import Flask, send_from_directory, jsonify
from flask_cors import CORS
from flask_jwt_extended import JWTManager
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from database import init_db
from routes.auth_routes import auth_bp
from routes.ticket_routes import ticket_bp
from routes.user_routes import user_bp
from routes.comment_routes import comment_bp
from routes.dashboard_routes import dashboard_bp
from routes.system_routes import system_bp
from routes.client_routes import client_bp

BASE_DIR = os.path.dirname(__file__)

app = Flask(
    __name__,
    static_folder=os.path.join(BASE_DIR, "static"),
    template_folder=os.path.join(BASE_DIR, "templates"),
)

# ── Configuration ──────────────────────────────────────────────────────────────
JWT_SECRET = os.environ.get("JWT_SECRET")
if not JWT_SECRET:
    # Fail loudly rather than silently falling back to a secret written to disk.
    # Render's disk resets on every deploy, so a disk-based fallback quietly
    # invalidates every session on every deploy with no clear error — this was
    # the cause of a hard-to-diagnose "everyone gets logged out" bug previously.
    raise RuntimeError(
        "JWT_SECRET environment variable is not set. Set it in your deployment "
        "environment (e.g. Render → Environment tab) before starting the app."
    )

app.config["JWT_SECRET_KEY"] = JWT_SECRET
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = 86400  # 24 hours
app.config["PROPAGATE_EXCEPTIONS"] = True

# CORS: locked to the actual frontend origin(s). Add more entries here if a
# custom domain is added later — a bare "*" would let any website's JS call
# this API using a visitor's stolen/leaked token.
ALLOWED_ORIGINS = [
    o.strip() for o in os.environ.get(
        "ALLOWED_ORIGINS", "https://tia-ticketing-1.onrender.com"
    ).split(",") if o.strip()
]
CORS(app, resources={r"/api/*": {"origins": ALLOWED_ORIGINS}})

jwt = JWTManager(app)

limiter = Limiter(get_remote_address, app=app, storage_uri="memory://", default_limits=[])

# ── Blueprints ─────────────────────────────────────────────────────────────────
app.register_blueprint(auth_bp,      url_prefix="/api/auth")
app.register_blueprint(ticket_bp,    url_prefix="/api/tickets")
app.register_blueprint(user_bp,      url_prefix="/api/users")
app.register_blueprint(comment_bp,   url_prefix="/api/comments")
app.register_blueprint(dashboard_bp, url_prefix="/api/dashboard")
app.register_blueprint(system_bp,    url_prefix="/api/system")
app.register_blueprint(client_bp,    url_prefix="/api/clients")

# Rate limit brute-force-prone auth endpoints specifically.
limiter.limit("10 per minute")(auth_bp)


@app.errorhandler(429)
def handle_rate_limit(e):
    return jsonify(error="Too many attempts. Please wait a minute and try again."), 429


# ── Serve SPA ──────────────────────────────────────────────────────────────────
@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_spa(path):
    static_file = os.path.join(BASE_DIR, "static", path)
    if path and os.path.exists(static_file):
        return send_from_directory(os.path.join(BASE_DIR, "static"), path)
    return send_from_directory(os.path.join(BASE_DIR, "static"), "index.html")


# ── Initialise DB on startup (works for both gunicorn and dev server) ──────────
with app.app_context():
    init_db()


if __name__ == "__main__":
    init_db()
    print("✅  TIA Ticketing System started at http://localhost:8080")
    app.run(debug=False, port=8080)

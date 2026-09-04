import hashlib
import secrets
from datetime import datetime, timedelta
from functools import wraps

from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from . import db


bp = Blueprint("auth", __name__)

RESET_TOKEN_TTL_MINUTES = 30
MIN_PASSWORD_LENGTH = 6


def hash_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@bp.before_app_request
def load_logged_in_user():
    user_id = session.get("user_id")
    if user_id is None:
        g.user = None
    else:
        g.user = db.query_one("SELECT * FROM users WHERE id = ? AND is_active IS TRUE", (user_id,))


def login_required(view):
    @wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            return redirect(url_for("auth.login"))
        return view(**kwargs)

    return wrapped_view


def admin_required(view):
    @wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            return redirect(url_for("auth.login"))
        if not g.user["is_admin"]:
            flash("Admin access is required.", "warning")
            return redirect(url_for("index"))
        return view(**kwargs)

    return wrapped_view


@bp.route("/login", methods=("GET", "POST"))
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        user = db.query_one("SELECT * FROM users WHERE (username = ? OR emp_id = ?) AND is_active IS TRUE", (username, username))

        if user is None or not check_password_hash(user["password_hash"], password):
            flash("Invalid username or password.", "danger")
        else:
            session.clear()
            session["user_id"] = user["id"]
            # Station Controllers primarily create SDMs; open the new-SDM page.
            if user["role"] == "STATION_CONTROLLER":
                return redirect(url_for("main.new_sdm"))
            return redirect(url_for("main.index"))

    return render_template("auth/login.html")


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/change-password", methods=("GET", "POST"))
@login_required
def change_password():
    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")
        if not check_password_hash(g.user["password_hash"], current_password):
            flash("Current password is incorrect.", "danger")
        elif len(new_password) < MIN_PASSWORD_LENGTH:
            flash(f"New password must be at least {MIN_PASSWORD_LENGTH} characters.", "danger")
        elif new_password != confirm_password:
            flash("New password and confirmation do not match.", "danger")
        else:
            db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(new_password), g.user["id"]))
            # A changed password invalidates any outstanding reset links.
            db.execute("DELETE FROM password_reset_tokens WHERE user_id = ?", (g.user["id"],))
            flash("Password changed.", "success")
            return redirect(url_for("index"))
    return render_template("auth/change_password.html")


@bp.route("/forgot-password", methods=("GET", "POST"))
def forgot_password():
    reset_url = None
    if request.method == "POST":
        emp_id = request.form.get("emp_id", "").strip()
        user = db.query_one("SELECT * FROM users WHERE emp_id = ? AND is_active IS TRUE", (emp_id,)) if emp_id else None
        if user:
            token = secrets.token_urlsafe(32)
            expires_at = (datetime.now() + timedelta(minutes=RESET_TOKEN_TTL_MINUTES)).replace(microsecond=0).isoformat(sep=" ")
            db.execute(
                """
                INSERT INTO password_reset_tokens (user_id, token_hash, expires_at, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (user["id"], hash_token(token), expires_at, db.now_iso()),
            )
            reset_url = url_for("auth.reset_password", token=token, _external=True)
            flash("A password reset link has been generated for that employment number.", "success")
        else:
            flash("No active account was found for that employment number.", "warning")
    return render_template("auth/forgot_password.html", reset_url=reset_url)


@bp.route("/reset-password/<token>", methods=("GET", "POST"))
def reset_password(token):
    reset = db.query_one(
        """
        SELECT t.id, t.user_id, t.expires_at, u.is_active AS user_active
        FROM password_reset_tokens t
        JOIN users u ON u.id = t.user_id
        WHERE t.token_hash = ? AND t.expires_at > ?
        """,
        (hash_token(token), db.now_iso()),
    )
    if not reset or not reset["user_active"]:
        flash("This reset link is invalid or has expired.", "danger")
        return redirect(url_for("auth.forgot_password"))

    if request.method == "POST":
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")
        if len(new_password) < MIN_PASSWORD_LENGTH:
            flash(f"New password must be at least {MIN_PASSWORD_LENGTH} characters.", "danger")
        elif new_password != confirm_password:
            flash("New password and confirmation do not match.", "danger")
        else:
            db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(new_password), reset["user_id"]))
            # Reset links are single-use: consume this one and any other outstanding ones.
            db.execute("DELETE FROM password_reset_tokens WHERE user_id = ?", (reset["user_id"],))
            flash("Password reset. Please log in with your new password.", "success")
            return redirect(url_for("auth.login"))
    return render_template("auth/reset_password.html")

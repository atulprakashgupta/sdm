from flask import Blueprint, Response, abort, flash, g, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash

from . import db, master_data, workflow
from .auth import admin_required


bp = Blueprint("admin", __name__)


@bp.route("/admin")
@admin_required
def admin_index():
    per_page = 20
    q = (request.args.get("q") or "").strip()
    page = max(1, request.args.get("page", 1, type=int) or 1)

    where = "WHERE is_active IS TRUE"
    params = []
    if q:
        where += " AND (name LIKE ? OR emp_id LIKE ? OR username LIKE ? OR role LIKE ?)"
        like = f"%{q}%"
        params = [like, like, like, like]

    total = db.query_one(f"SELECT COUNT(*) AS n FROM users {where}", tuple(params))["n"]
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, total_pages)
    users = db.query_all(
        f"SELECT * FROM users {where} ORDER BY name, emp_id LIMIT ? OFFSET ?",
        tuple(params) + (per_page, (page - 1) * per_page),
    )

    stages = workflow.enabled_stages()
    stage_users = {}
    for stage in stages:
        stage_users[stage["role"]] = db.query_all(
            "SELECT id, name, emp_id FROM users WHERE role = ? AND is_active IS TRUE ORDER BY name, emp_id",
            (stage["role"],),
        )

    reasons = db.query_all("SELECT * FROM reasons ORDER BY id")
    return render_template(
        "admin/index.html",
        users=users,
        stages=stages,
        stage_users=stage_users,
        reasons=reasons,
        q=q,
        page=page,
        total_pages=total_pages,
        total=total,
    )


@bp.route("/admin/users", methods=("POST",))
@admin_required
def add_user():
    try:
        db.execute(
            """
            INSERT INTO users
            (username, password_hash, name, emp_id, designation, role, is_admin, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request.form["username"].strip(),
                generate_password_hash(request.form["password"]),
                request.form["name"].strip(),
                request.form["emp_id"].strip(),
                request.form["designation"].strip(),
                request.form["role"],
                bool(request.form.get("is_admin")),
                db.now_iso(),
            ),
        )
        flash("User added.", "success")
    except db.integrity_error():
        existing_user = db.query_one(
            "SELECT * FROM users WHERE username = ? OR emp_id = ?",
            (request.form["username"].strip(), request.form["emp_id"].strip()),
        )
        if existing_user:
            db.execute(
                """
                UPDATE users
                SET username = ?, password_hash = ?, name = ?, emp_id = ?, designation = ?,
                    role = ?, is_admin = ?, is_active = TRUE
                WHERE id = ?
                """,
                (
                    request.form["username"].strip(),
                    generate_password_hash(request.form["password"]),
                    request.form["name"].strip(),
                    request.form["emp_id"].strip(),
                    request.form["designation"].strip(),
                    request.form["role"],
                    bool(request.form.get("is_admin")),
                    existing_user["id"],
                ),
            )
            flash("Existing inactive user restored/updated.", "success")
        else:
            flash("Username or Emp ID already exists.", "danger")
    return redirect(url_for("admin.admin_index"))


@bp.route("/admin/users/<int:user_id>/delete", methods=("POST",))
@admin_required
def delete_user(user_id):
    if user_id == g.user["id"]:
        flash("You cannot delete your own login.", "warning")
        return redirect(url_for("admin.admin_index"))

    user = db.query_one("SELECT * FROM users WHERE id = ?", (user_id,))
    if not user:
        abort(404)
    db.execute("UPDATE users SET is_active = FALSE WHERE id = ?", (user_id,))
    flash("User deleted.", "success")
    return redirect(url_for("admin.admin_index"))


@bp.route("/admin/line", methods=("POST",))
@admin_required
def add_line():
    try:
        db.execute("INSERT INTO lines (name) VALUES (?)", (request.form["name"].strip(),))
        flash("Line added.", "success")
    except db.integrity_error():
        db.execute("UPDATE lines SET is_active = TRUE WHERE name = ?", (request.form["name"].strip(),))
        flash("Line restored.", "success")
    return redirect(url_for("admin.admin_index"))


@bp.route("/admin/line/<int:line_id>/delete", methods=("POST",))
@admin_required
def delete_line(line_id):
    line = db.query_one("SELECT * FROM lines WHERE id = ?", (line_id,))
    if not line:
        abort(404)
    database = db.get_db()
    database.execute("UPDATE lines SET is_active = FALSE WHERE id = ?", (line_id,))
    database.execute("UPDATE stations SET is_active = FALSE WHERE line_id = ?", (line_id,))
    database.commit()
    flash("Line and its stations deleted.", "success")
    return redirect(url_for("admin.admin_index"))


@bp.route("/admin/station", methods=("POST",))
@admin_required
def add_station():
    try:
        db.execute("INSERT INTO stations (line_id, name) VALUES (?, ?)", (request.form["line_id"], request.form["name"].strip()))
        flash("Station added.", "success")
    except db.integrity_error():
        db.execute(
            "UPDATE stations SET is_active = TRUE WHERE line_id = ? AND name = ?",
            (request.form["line_id"], request.form["name"].strip()),
        )
        flash("Station restored.", "success")
    return redirect(url_for("admin.admin_index"))


@bp.route("/admin/station/<int:station_id>/delete", methods=("POST",))
@admin_required
def delete_station(station_id):
    station = db.query_one("SELECT * FROM stations WHERE id = ?", (station_id,))
    if not station:
        abort(404)
    db.execute("UPDATE stations SET is_active = FALSE WHERE id = ?", (station_id,))
    flash("Station deleted.", "success")
    return redirect(url_for("admin.admin_index"))


@bp.route("/admin/contractor", methods=("POST",))
@admin_required
def add_contractor():
    try:
        db.execute("INSERT INTO contractors (name) VALUES (?)", (request.form["name"].strip(),))
        flash("Contractor added.", "success")
    except db.integrity_error():
        flash("Contractor already exists.", "danger")
    return redirect(url_for("admin.admin_index"))


@bp.route("/admin/contractor/<int:contractor_id>/delete", methods=("POST",))
@admin_required
def delete_contractor(contractor_id):
    db.execute("DELETE FROM contractors WHERE id = ?", (contractor_id,))
    flash("Contractor deleted.", "success")
    return redirect(url_for("admin.admin_index"))


@bp.route("/admin/reason", methods=("POST",))
@admin_required
def add_reason():
    name = request.form["name"].strip()
    db.execute("INSERT INTO reasons (name, code, needs_public_complaint, needs_overcharging_status, needs_inspection_details) VALUES (?, ?, ?, ?, ?)",
              (name, name.upper().replace(' ', '_'), 0, 0, 0))
    flash("Reason added.", "success")
    return redirect(url_for("admin.admin_index"))


@bp.route("/admin/reason/<int:reason_id>/delete", methods=("POST",))
@admin_required
def delete_reason(reason_id):
    db.execute("DELETE FROM reasons WHERE id = ?", (reason_id,))
    flash("Reason deleted.", "success")
    return redirect(url_for("admin.admin_index"))


@bp.route("/admin/workflow", methods=("POST",))
@admin_required
def update_workflow():
    database = db.get_db()
    for stage in workflow.enabled_stages():
        field = f"default_user_{stage['step_index']}"
        selected_user_id = request.form.get(field) or None
        if selected_user_id:
            user = db.query_one("SELECT * FROM users WHERE id = ? AND role = ? AND is_active IS TRUE", (selected_user_id, stage["role"]))
            if not user:
                flash(f"Invalid default officer for {stage['label']}.", "danger")
                return redirect(url_for("admin.admin_index"))
        database.execute("UPDATE workflow_stages SET default_user_id = ? WHERE step_index = ?", (selected_user_id, stage["step_index"]))
    database.commit()
    flash("Workflow defaults updated.", "success")
    return redirect(url_for("admin.admin_index"))


# ---------------------------------------------------------------------------
# Bulk master-data import
# ---------------------------------------------------------------------------

@bp.route("/admin/import")
@admin_required
def master_import_page():
    return render_template("admin/import.html", kinds=master_data.KIND_LABELS)


@bp.route("/admin/import/template/<kind>")
@admin_required
def master_import_template(kind):
    if kind not in master_data.KIND_LABELS:
        abort(404)
    content = master_data.build_template_bytes(kind)
    return Response(
        content,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={master_data.KIND_TEMPLATE_FILENAME[kind]}"},
    )


@bp.route("/admin/import/upload", methods=("POST",))
@admin_required
def master_import_upload():
    kind = request.form.get("kind")
    if kind not in master_data.KIND_LABELS:
        flash("Please choose a list type (Employees, Lines, Stations or Contractors).", "danger")
        return redirect(url_for("admin.master_import_page"))

    file = request.files.get("file")
    if not file or not file.filename:
        flash("Please choose a file to upload.", "danger")
        return redirect(url_for("admin.master_import_page"))

    content = file.read(master_data.MAX_UPLOAD_BYTES + 1)
    if len(content) > master_data.MAX_UPLOAD_BYTES:
        flash("The file is larger than 5 MB. Please split it into smaller files.", "danger")
        return redirect(url_for("admin.master_import_page"))

    parsed = master_data.parse_file(kind, file.filename, content)
    if parsed["error"]:
        flash(parsed["error"], "danger")
        return redirect(url_for("admin.master_import_page"))
    if not parsed["rows"]:
        flash("No data rows were found in the file.", "warning")
        return redirect(url_for("admin.master_import_page"))

    deactivate = request.form.get("deactivate_missing") == "1"
    preview = master_data.plan(kind, parsed["rows"], deactivate_missing=deactivate)
    staging_id = master_data.save_staging(kind, file.filename, content, deactivate_missing=deactivate)

    return render_template(
        "admin/import_preview.html",
        kind=kind,
        kind_label=master_data.KIND_LABELS[kind],
        preview=preview,
        staging_id=staging_id,
        warnings=parsed["warnings"],
    )


@bp.route("/admin/import/<staging_id>/apply", methods=("POST",))
@admin_required
def master_import_apply(staging_id):
    loaded = master_data.load_staging(staging_id)
    if loaded is None:
        flash("The upload has expired or is no longer available. Please upload the file again.", "warning")
        return redirect(url_for("admin.master_import_page"))

    meta, content = loaded
    kind = meta["kind"]
    parsed = master_data.parse_file(kind, meta["filename"], content)
    if parsed["error"]:
        master_data.discard_staging(staging_id)
        flash(parsed["error"], "danger")
        return redirect(url_for("admin.master_import_page"))

    preview = master_data.plan(kind, parsed["rows"], deactivate_missing=meta["deactivate_missing"])
    if preview["has_errors"]:
        master_data.discard_staging(staging_id)
        flash(f"{preview['counts']['ERROR']} row(s) now have errors. Nothing was changed. Fix the rows and upload again.", "danger")
        return redirect(url_for("admin.master_import_page"))

    try:
        counts = master_data.apply(kind, parsed["rows"], deactivate_missing=meta["deactivate_missing"])
    except Exception as exc:
        flash(f"The import could not be saved: {exc}", "danger")
        return redirect(url_for("admin.master_import_page"))
    finally:
        master_data.discard_staging(staging_id)

    label = master_data.KIND_LABELS[kind]
    flash(
        f"{label}: {counts['NEW']} added, {counts['UPDATE']} updated/reactivated, "
        f"{counts['DEACTIVATE']} deactivated, {counts['NO_CHANGE']} unchanged.",
        "success",
    )
    return redirect(url_for("admin.admin_index"))

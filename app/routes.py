import json
from datetime import date, datetime

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)

from . import db, workflow
from .auth import login_required
from .utils import attachment_url_path, parse_reason_payload, reason_payload_from_row, save_attachments


bp = Blueprint("main", __name__)


@bp.route("/")
@login_required
def index():
    assigned_only = request.args.get("assigned") == "1"
    view_mode = request.args.get("view", "dashboard")  # dashboard, assigned, or reports
    
    params = []
    where_clause = ""
    
    if assigned_only or view_mode == "assigned":
        # Show both assigned AND returned SDMs for the current user
        where_clause = """WHERE (
            (s.current_assignee_id = ? AND s.status NOT IN ('CANCELLED', 'CLOSED', 'COMPLETED', 'REJECTED'))
            OR s.status = 'RETURNED'
        )"""
        params.append(g.user["id"])

    all_sdms = db.query_all(
        f"""
        SELECT s.*, l.name AS line_name, st.name AS station_name, r.name AS reason_name, c.name AS contractor_name
        FROM sdm s
        JOIN lines l ON l.id = s.line_id
        JOIN stations st ON st.id = s.station_id
        JOIN reasons r ON r.id = s.reason_id
        JOIN contractors c ON c.id = s.contractor_id
        {where_clause}
        ORDER BY s.created_at DESC
        """,
        params,
    )
    
    # Determine which view to show
    if assigned_only or view_mode == "assigned":
        return render_template("dashboard.html", all_sdms=all_sdms, view_mode="assigned")
    elif view_mode == "reports":
        return render_template("dashboard.html", all_sdms=all_sdms, view_mode="reports", **form_options())
    else:
        return render_template("dashboard.html", all_sdms=all_sdms, view_mode="dashboard")


def form_options():
    return {
        "lines": db.query_all("SELECT * FROM lines WHERE is_active IS TRUE ORDER BY name"),
        "stations": db.query_all("SELECT * FROM stations WHERE is_active IS TRUE ORDER BY name"),
        "contractors": db.query_all("SELECT * FROM contractors WHERE is_active IS TRUE ORDER BY name"),
        "reasons": db.query_all("SELECT * FROM reasons WHERE is_active IS TRUE ORDER BY id"),
        "today": date.today().isoformat(),
    }


def validate_sdm_form(form):
    required = ["foil_no", "memo_date", "line_id", "station_id", "contractor_id", "staff_name", "staff_emp_id", "reason_id"]
    missing = [field for field in required if not form.get(field)]
    if missing:
        return "Please fill all required fields."

    today = date.today().isoformat()
    if form.get("memo_date") > today:
        return "Future dates are not allowed."

    reason = db.query_one("SELECT * FROM reasons WHERE id = ? AND is_active IS TRUE", (form.get("reason_id"),))
    if not reason:
        return "Please select a valid reason."
    if reason["needs_public_complaint"] and not form.get("complaint_no", "").strip():
        return "Complaint No. is required for Public Complaint."
    if reason["needs_overcharging_status"] and not form.get("overcharging_status", "").strip():
        return "Please select whether overcharging is proved or not proved."
    if reason["needs_inspection_details"]:
        inspection_required = ["location_no", "date_of_check", "time_of_check", "net_amount_eos", "amount_found", "result"]
        if any(not form.get(field, "").strip() for field in inspection_required):
            return "Please complete the required inspection/cash checking details."
        if form.get("date_of_check") > today:
            return "Future dates are not allowed."
    return None


def sdm_from_form(form, user_id):
    return {
        "foil_no": form["foil_no"].strip(),
        "memo_date": form["memo_date"],
        "line_id": int(form["line_id"]),
        "station_id": int(form["station_id"]),
        "contractor_id": int(form["contractor_id"]),
        "staff_name": form["staff_name"].strip(),
        "staff_emp_id": form["staff_emp_id"].strip(),
        "reason_id": int(form["reason_id"]),
        "reason_payload": json.dumps(parse_reason_payload(form)),
        "current_assignee_id": user_id,
    }


@bp.route("/sdm/new", methods=("GET", "POST"))
@login_required
def new_sdm():
    if g.user["role"] not in ("STATION_CONTROLLER", "STATION_MANAGER") and not g.user["is_admin"]:
        flash("Only Station Controller and Station Manager users can create SDMs.", "warning")
        return redirect(url_for("index"))

    if request.method == "POST":
        error = validate_sdm_form(request.form)
        if error:
            flash(error, "danger")
            return render_template("sdm/form.html", sdm=None, payload={}, **form_options())

        data = sdm_from_form(request.form, g.user["id"])
        remarks = request.form.get("remarks", "").strip()
        creator_stage = workflow.stage_for_role(g.user["role"]) if not g.user["is_admin"] else workflow.stage_for_index(0)
        next_stage = workflow.next_stage(creator_stage["step_index"] if creator_stage else 0)
        assignee = workflow.select_forward_assignee(g.user, next_stage)
        if not assignee:
            flash("No active officer is configured for the next workflow stage.", "danger")
            return render_template("sdm/form.html", sdm=None, payload={}, **form_options())
        sdm_no = workflow.next_sdm_number()
        status = "PENDING"
        current_step_index = next_stage["step_index"]
        current_role = next_stage["role"]
        data["current_assignee_id"] = assignee["id"]
        submitted_at = db.now_iso()

        try:
            database = db.get_db()

            cursor = database.execute(
                """
                INSERT INTO sdm
                (sdm_no, foil_no, memo_date, line_id, station_id, contractor_id, staff_name, staff_emp_id,
                 reason_id, reason_payload, status, current_step_index, current_role, current_assignee_id,
                 created_by, created_at, submitted_at, remarks)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id
                """,
                (
                    sdm_no,
                    data["foil_no"],
                    data["memo_date"],
                    data["line_id"],
                    data["station_id"],
                    data["contractor_id"],
                    data["staff_name"],
                    data["staff_emp_id"],
                    data["reason_id"],
                    data["reason_payload"],
                    status,
                    current_step_index,
                    current_role,
                    data["current_assignee_id"],
                    g.user["id"],
                    db.now_iso(),
                    submitted_at,
                    remarks,
                ),
            )
            sdm_id = cursor.fetchone()["id"]
            save_attachments(sdm_id, request.files.getlist("attachments"), g.user["id"])
            workflow.add_event(
                sdm_id,
                "CREATED",
                g.user["id"],
                to_step_index=current_step_index,
                to_role=current_role,
                assigned_to_id=data["current_assignee_id"],
                note=remarks or None,
            )
            database.commit()
            flash("SDM submitted.", "success")
            return redirect(url_for("main.view_sdm", sdm_id=sdm_id))
        except db.integrity_error():
            db.get_db().rollback()
            flash("Foil No. has already been used. Enter a different Foil No.", "danger")
        except ValueError as exc:
            db.get_db().rollback()
            flash(str(exc), "danger")

    return render_template("sdm/form.html", sdm=None, payload={}, **form_options())


def get_sdm_or_404(sdm_id):
    sdm = db.query_one(
        """
        SELECT s.*, l.name AS line_name, st.name AS station_name, c.name AS contractor_name,
               r.name AS reason_name, r.code AS reason_code,
               r.needs_public_complaint, r.needs_overcharging_status, r.needs_inspection_details,
               u.name AS assignee_name, u.designation AS assignee_designation,
               creator.name AS creator_name, creator.emp_id AS creator_emp_id, creator.designation AS creator_designation, creator.role AS creator_role
        FROM sdm s
        JOIN lines l ON l.id = s.line_id
        JOIN stations st ON st.id = s.station_id
        JOIN contractors c ON c.id = s.contractor_id
        JOIN reasons r ON r.id = s.reason_id
        LEFT JOIN users u ON u.id = s.current_assignee_id
        LEFT JOIN users creator ON creator.id = s.created_by
        WHERE s.id = ?
        """,
        (sdm_id,),
    )
    if not sdm:
        abort(404)
    if not workflow.can_view_sdm(g.user, sdm):
        abort(403)
    return sdm


def sdm_related(sdm_id):
    attachments = db.query_all(
        """
        SELECT a.*, u.name AS uploaded_by_name
        FROM attachments a
        JOIN users u ON u.id = a.uploaded_by
        WHERE a.sdm_id = ? AND a.deleted_at IS NULL
        ORDER BY a.uploaded_at
        """,
        (sdm_id,),
    )
    events = db.query_all(
        """
        SELECT e.*, actor.name AS actor_name, actor.emp_id AS actor_emp_id, actor.designation AS actor_designation,
               actor.role AS actor_role,
               assigned.name AS assigned_name
        FROM workflow_events e
        JOIN users actor ON actor.id = e.actor_id
        LEFT JOIN users assigned ON assigned.id = e.assigned_to_id
        WHERE e.sdm_id = ?
        ORDER BY e.id
        """,
        (sdm_id,),
    )
    return attachments, events


def display_sdm_number(sdm_no):
    return sdm_no.replace("SDM No. - ", "") if sdm_no else "Draft"


def display_event_time(value):
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value
    return parsed.strftime("%d.%m.%Y - %H:%M:%S")


def note_groups_for_sdm(sdm, events):
    labels = {
        "STATION_CONTROLLER": "Note/Remarks by Station Controller",
        "STATION_MANAGER": "Note/Remarks by Station Manager",
        "LINE_MANAGER": "Note/Remarks by Line Manager",
        "DY_HOD": "Note/Remarks by Dy. HOD",
        "CONCERNED_CELL": "Note/Remarks by Concerned Cell",
    }
    groups = [{"role": role, "label": label, "notes": [], "submitted_at": ""} for role, label in labels.items()]
    notes_by_role = {group["role"]: group["notes"] for group in groups}
    groups_by_role = {group["role"]: group for group in groups}
    seen_notes = set()

    # Add creator's remarks from the SDM record (this is the latest remarks)
    if sdm["remarks"]:
        creator_role = None
        # Check if creator_role is available in the sdm row
        try:
            creator_role = sdm["creator_role"]
        except (KeyError, IndexError, TypeError):
            creator_role = None
        if not creator_role and sdm["creator_designation"]:
            creator_role = db.employee_role_from_designation(sdm["creator_designation"])
        if creator_role in notes_by_role:
            # Only add the latest remarks, don't append - replace with single latest remark
            notes_by_role[creator_role] = [sdm["remarks"]]
            groups_by_role[creator_role]["submitted_at"] = display_event_time(sdm["submitted_at"] or sdm["created_at"])
            seen_notes.add((creator_role, sdm["remarks"]))

    for event in events:
        note = (event["note"] or "").strip()
        actor_role = event["actor_role"]
        # Skip if this is the same as the latest creator remarks to avoid duplication
        if note and actor_role in notes_by_role and (actor_role, note) not in seen_notes:
            # If this is the creator's role and the note matches the latest remarks, skip it
            if actor_role == creator_role and note == sdm["remarks"]:
                continue
            notes_by_role[actor_role].append(note)
            groups_by_role[actor_role]["submitted_at"] = display_event_time(event["created_at"])
            seen_notes.add((actor_role, note))

    return groups


@bp.route("/sdm/<int:sdm_id>")
@login_required
def view_sdm(sdm_id):
    sdm = get_sdm_or_404(sdm_id)
    attachments, events = sdm_related(sdm_id)
    next_stage = workflow.next_stage(sdm["current_step_index"])
    next_users = workflow.users_for_role(next_stage["role"]) if next_stage else []
    return render_template(
        "sdm/detail.html",
        sdm=sdm,
        payload=reason_payload_from_row(sdm),
        attachments=attachments,
        attachment_url_path=attachment_url_path,
        events=events,
        note_groups=note_groups_for_sdm(sdm, events),
        next_stage=next_stage,
        next_users=next_users,
        can_edit=workflow.can_edit_sdm(g.user, sdm),
        can_act=workflow.can_act_on_sdm(g.user, sdm),
        can_cancel=workflow.can_cancel_sdm(g.user, sdm),
        display_sdm_number=display_sdm_number,
    )


@bp.route("/sdm/<int:sdm_id>/edit", methods=("GET", "POST"))
@login_required
def edit_sdm(sdm_id):
    sdm = get_sdm_or_404(sdm_id)
    if not workflow.can_edit_sdm(g.user, sdm):
        abort(403)

    if request.method == "POST":
        error = validate_sdm_form(request.form)
        if error:
            flash(error, "danger")
            return render_template("sdm/form.html", sdm=sdm, payload=parse_reason_payload(request.form), **form_options())

        data = sdm_from_form(request.form, sdm["current_assignee_id"])
        database = db.get_db()
        try:
            database.execute(
                """
                UPDATE sdm
                SET foil_no = ?, memo_date = ?, line_id = ?, station_id = ?, contractor_id = ?,
                    staff_name = ?, staff_emp_id = ?, reason_id = ?, reason_payload = ?, remarks = ?
                WHERE id = ?
                """,
                (
                    data["foil_no"],
                    data["memo_date"],
                    data["line_id"],
                    data["station_id"],
                    data["contractor_id"],
                    data["staff_name"],
                    data["staff_emp_id"],
                    data["reason_id"],
                    data["reason_payload"],
                    request.form.get("remarks", "").strip(),
                    sdm_id,
                ),
            )
            save_attachments(sdm_id, request.files.getlist("attachments"), g.user["id"])
            workflow.add_event(sdm_id, "EDITED", g.user["id"], note="SDM form details updated.")

            if sdm["status"] in {"DRAFT", "RETURNED"}:
                next_stage = workflow.next_stage(sdm["current_step_index"])
                # Use superior relationship for routing
                if g.user["superior_id"]:
                    assignee = db.query_one("SELECT * FROM users WHERE id = ? AND is_active IS TRUE", (g.user["superior_id"],))
                else:
                    assignee = workflow.default_assignee_for_stage(next_stage)
                if not assignee:
                    raise ValueError("No active officer is configured for the next workflow stage.")
                sdm_no = sdm["sdm_no"] or workflow.next_sdm_number()
                database.execute(
                    """
                    UPDATE sdm
                    SET sdm_no = ?, status = 'PENDING', current_step_index = ?, current_role = ?,
                        current_assignee_id = ?, submitted_at = COALESCE(submitted_at, ?)
                    WHERE id = ?
                    """,
                    (
                        sdm_no,
                        next_stage["step_index"],
                        next_stage["role"],
                        assignee["id"],
                        db.now_iso(),
                        sdm_id,
                    ),
                )
                workflow.add_event(
                    sdm_id,
                    "CREATED",
                    g.user["id"],
                    to_step_index=next_stage["step_index"],
                    to_role=next_stage["role"],
                    assigned_to_id=assignee["id"],
                )
            database.commit()
            flash("SDM updated.", "success")
            return redirect(url_for("main.view_sdm", sdm_id=sdm_id))
        except db.integrity_error():
            database.rollback()
            flash("Foil No. has already been used. Enter a different Foil No.", "danger")
        except ValueError as exc:
            database.rollback()
            flash(str(exc), "danger")

    return render_template("sdm/form.html", sdm=sdm, payload=reason_payload_from_row(sdm), **form_options())


@bp.route("/sdm/<int:sdm_id>/attachment/<int:attachment_id>/delete", methods=("POST",))
@login_required
def delete_attachment(sdm_id, attachment_id):
    sdm = get_sdm_or_404(sdm_id)
    if not workflow.can_delete_attachment(g.user, sdm):
        abort(403)
    db.execute(
        "UPDATE attachments SET deleted_at = ? WHERE id = ? AND sdm_id = ?",
        (db.now_iso(), attachment_id, sdm_id),
    )
    flash("Attachment removed from draft.", "success")
    return redirect(url_for("main.edit_sdm", sdm_id=sdm_id))


@bp.route("/sdm/<int:sdm_id>/action", methods=("POST",))
@login_required
def sdm_action(sdm_id):
    sdm = get_sdm_or_404(sdm_id)
    if not workflow.can_act_on_sdm(g.user, sdm):
        abort(403)

    action = request.form.get("action")
    note = request.form.get("note", "").strip()
    database = db.get_db()

    # Handle penalty amount
    penalty_amount = request.form.get("penalty_amount")
    penalty_amount_modified = request.form.get("penalty_amount_modified")

    # Line Manager must provide penalty amount (can be 0)
    if sdm["current_role"] == "LINE_MANAGER" and (penalty_amount is None or penalty_amount.strip() == ""):
        flash("Penalty amount is required for Line Manager. Enter 0 if no penalty.", "danger")
        return redirect(url_for("main.view_sdm", sdm_id=sdm_id))

    if penalty_amount:
        database.execute(
            "UPDATE sdm SET penalty_amount = ? WHERE id = ?",
            (float(penalty_amount) if penalty_amount else None, sdm_id),
        )
    elif penalty_amount_modified and sdm["current_role"] in ["DY_HOD", "HOD", "CONCERNED_CELL"]:
        database.execute(
            "UPDATE sdm SET penalty_amount = ?, penalty_modified_by = ?, penalty_modified_at = ? WHERE id = ?",
            (float(penalty_amount_modified) if penalty_amount_modified else None, g.user["id"], db.now_iso(), sdm_id),
        )

    if action == "forward":
        next_stage = workflow.next_stage(sdm["current_step_index"])
        if not next_stage:
            database.execute("UPDATE sdm SET status = 'CLOSED', closed_at = ? WHERE id = ?", (db.now_iso(), sdm_id))
            workflow.add_event(sdm_id, "CLOSED", g.user["id"], from_step_index=sdm["current_step_index"], from_role=sdm["current_role"], note=note)
            database.commit()
            flash("SDM closed.", "success")
            return redirect(url_for("main.view_sdm", sdm_id=sdm_id))

        try:
            assignee = workflow.find_alternate_assignee(
                request.form.get("alternate_name"),
                request.form.get("alternate_emp_id"),
                next_stage["role"],
            )
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("main.view_sdm", sdm_id=sdm_id))

        if not assignee:
            assignee = workflow.select_forward_assignee(g.user, next_stage)

        if not assignee:
            flash("Select a valid officer for the next stage.", "danger")
            return redirect(url_for("main.view_sdm", sdm_id=sdm_id))

        forward_note = note

        database.execute(
            """
            UPDATE sdm
            SET status = 'PENDING', current_step_index = ?, current_role = ?, current_assignee_id = ?
            WHERE id = ?
            """,
            (next_stage["step_index"], next_stage["role"], assignee["id"], sdm_id),
        )
        workflow.add_event(
            sdm_id,
            "FORWARDED",
            g.user["id"],
            from_step_index=sdm["current_step_index"],
            to_step_index=next_stage["step_index"],
            from_role=sdm["current_role"],
            to_role=next_stage["role"],
            assigned_to_id=assignee["id"],
            note=forward_note,
        )
        database.commit()
        flash("SDM forwarded.", "success")
        return redirect(url_for("main.view_sdm", sdm_id=sdm_id))

    if action == "accept":
        database.execute("UPDATE sdm SET status = 'COMPLETED', closed_at = ? WHERE id = ?", (db.now_iso(), sdm_id))
        workflow.add_event(sdm_id, "ACCEPTED", g.user["id"], from_step_index=sdm["current_step_index"], from_role=sdm["current_role"], note=note)
        database.commit()
        flash("SDM accepted and completed.", "success")
        return redirect(url_for("main.view_sdm", sdm_id=sdm_id))

    if action == "reject":
        database.execute("UPDATE sdm SET status = 'REJECTED', closed_at = ? WHERE id = ?", (db.now_iso(), sdm_id))
        workflow.add_event(sdm_id, "REJECTED", g.user["id"], from_step_index=sdm["current_step_index"], from_role=sdm["current_role"], note=note)
        database.commit()
        flash("SDM rejected.", "success")
        return redirect(url_for("main.view_sdm", sdm_id=sdm_id))

    if action == "return":
        previous_stage = workflow.previous_stage(sdm["current_step_index"])
        if not previous_stage:
            flash("There is no previous officer to return this SDM to.", "warning")
            return redirect(url_for("main.view_sdm", sdm_id=sdm_id))

        prior_forward = workflow.latest_forward_event_to_step(sdm_id, sdm["current_step_index"])
        previous_assignee_id = prior_forward["actor_id"] if prior_forward else sdm["created_by"]
        database.execute(
            """
            UPDATE sdm
            SET status = 'RETURNED', current_step_index = ?, current_role = ?, current_assignee_id = ?
            WHERE id = ?
            """,
            (previous_stage["step_index"], previous_stage["role"], previous_assignee_id, sdm_id),
        )
        workflow.add_event(
            sdm_id,
            "RETURNED",
            g.user["id"],
            from_step_index=sdm["current_step_index"],
            to_step_index=previous_stage["step_index"],
            from_role=sdm["current_role"],
            to_role=previous_stage["role"],
            assigned_to_id=previous_assignee_id,
            note=note,
        )
        database.commit()
        flash("SDM returned to the immediately previous officer.", "success")
        return redirect(url_for("main.view_sdm", sdm_id=sdm_id))

    if action == "cancel":
        if not workflow.can_cancel_sdm(g.user, sdm):
            abort(403)
        database.execute(
            "UPDATE sdm SET status = 'CANCELLED', cancelled_at = ? WHERE id = ?",
            (db.now_iso(), sdm_id),
        )
        workflow.add_event(sdm_id, "CANCELLED", g.user["id"], from_step_index=sdm["current_step_index"], from_role=sdm["current_role"], note=note)
        database.commit()
        flash("SDM cancelled. Its SDM No. and Foil No. remain permanently reserved.", "success")
        return redirect(url_for("main.view_sdm", sdm_id=sdm_id))

    abort(400)


@bp.route("/sdm/<int:sdm_id>/print")
@login_required
def print_sdm(sdm_id):
    sdm = get_sdm_or_404(sdm_id)
    attachments, events = sdm_related(sdm_id)
    return render_template(
        "sdm/print.html",
        sdm=sdm,
        payload=reason_payload_from_row(sdm),
        attachments=attachments,
        events=events,
        note_groups=note_groups_for_sdm(sdm, events),
        display_sdm_number=display_sdm_number,
    )


@bp.route("/uploads/<int:sdm_id>/<path:filename>")
@login_required
def uploaded_file(sdm_id, filename):
    sdm = get_sdm_or_404(sdm_id)
    if not workflow.can_view_sdm(g.user, sdm):
        abort(403)
    return send_from_directory(f"{current_app.config['UPLOAD_FOLDER']}/{sdm_id}", filename)


@bp.route("/export.csv")
@login_required
def export_csv():
    import csv
    import io
    
    # Only allow CONCERNED_CELL and admins to export
    if g.user["role"] != "CONCERNED_CELL" and not g.user["is_admin"]:
        abort(403)
    
    params = []
    where_clause = ""
    
    # Apply filters from request args
    clauses = ["1 = 1"]
    if request.args.get("from_date"):
        clauses.append("s.memo_date >= ?")
        params.append(request.args["from_date"])
    if request.args.get("to_date"):
        clauses.append("s.memo_date <= ?")
        params.append(request.args["to_date"])
    for key, column in [("line_id", "s.line_id"), ("station_id", "s.station_id"), ("reason_id", "s.reason_id")]:
        if request.args.get(key):
            clauses.append(f"{column} = ?")
            params.append(request.args[key])
    if request.args.get("status"):
        clauses.append("s.status = ?")
        params.append(request.args["status"])
    
    where_clause = "WHERE " + " AND ".join(clauses)
    
    rows = db.query_all(
        f"""
        SELECT s.*, l.name AS line_name, st.name AS station_name, c.name AS contractor_name,
               r.name AS reason_name, u.name AS assignee_name
        FROM sdm s
        JOIN lines l ON l.id = s.line_id
        JOIN stations st ON st.id = s.station_id
        JOIN contractors c ON c.id = s.contractor_id
        JOIN reasons r ON r.id = s.reason_id
        LEFT JOIN users u ON u.id = s.current_assignee_id
        {where_clause}
        ORDER BY s.created_at DESC
        """,
        params,
    )
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["SDM No.", "Foil No.", "Date", "Line", "Station", "Contractor", "Staff", "Reason", "Status", "Current Officer"])
    for row in rows:
        writer.writerow(
            [
                row["sdm_no"],
                row["foil_no"],
                row["memo_date"],
                row["line_name"],
                row["station_name"],
                row["contractor_name"],
                row["staff_name"],
                row["reason_name"],
                row["status"],
                row["assignee_name"],
            ]
        )
    
    response = current_app.make_response(output.getvalue())
    response.headers["Content-Disposition"] = "attachment; filename=sdm_report.csv"
    response.headers["Content-Type"] = "text/csv"
    return response

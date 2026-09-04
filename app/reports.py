import csv
import io

from flask import Blueprint, Response, abort, g, render_template, request

from . import db
from .auth import login_required
from .routes import form_options


bp = Blueprint("reports", __name__)


def report_rows(filters):
    clauses = ["1 = 1"]
    params = []
    if filters.get("from_date"):
        clauses.append("s.memo_date >= ?")
        params.append(filters["from_date"])
    if filters.get("to_date"):
        clauses.append("s.memo_date <= ?")
        params.append(filters["to_date"])
    for key, column in [("line_id", "s.line_id"), ("station_id", "s.station_id"), ("contractor_id", "s.contractor_id"), ("reason_id", "s.reason_id")]:
        if filters.get(key):
            clauses.append(f"{column} = ?")
            params.append(filters[key])
    if filters.get("status"):
        clauses.append("s.status = ?")
        params.append(filters["status"])

    return db.query_all(
        f"""
        SELECT s.*, l.name AS line_name, st.name AS station_name, c.name AS contractor_name,
               r.name AS reason_name, u.name AS assignee_name
        FROM sdm s
        JOIN lines l ON l.id = s.line_id
        JOIN stations st ON st.id = s.station_id
        JOIN contractors c ON c.id = s.contractor_id
        JOIN reasons r ON r.id = s.reason_id
        LEFT JOIN users u ON u.id = s.current_assignee_id
        WHERE {' AND '.join(clauses)}
        ORDER BY s.created_at DESC
        """,
        params,
    )


@bp.route("/reports")
@login_required
def reports():
    if g.user["role"] != "CONCERNED_CELL" and not g.user["is_admin"]:
        abort(403)
    rows = report_rows(request.args)
    return render_template("reports/index.html", rows=rows, filters=request.args, **form_options())


@bp.route("/reports.csv")
@login_required
def reports_csv():
    if g.user["role"] != "CONCERNED_CELL" and not g.user["is_admin"]:
        abort(403)
    rows = report_rows(request.args)
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
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=sdm-report.csv"})

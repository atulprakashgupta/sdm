import io
import re

import pytest

from app import db as db_module
from app import master_data

STAGING_RE = re.compile(r"/admin/import/([0-9a-f]{32})/apply")


def login_admin(client):
    return client.post("/login", data={"username": "admin", "password": "123"}, follow_redirects=False)


def upload(client, kind, csv_text, deactivate=False):
    data = {"kind": kind, "file": (io.BytesIO(csv_text.encode("utf-8")), f"{kind}.csv")}
    if deactivate:
        data["deactivate_missing"] = "1"
    return client.post("/admin/import/upload", data=data, content_type="multipart/form-data", follow_redirects=False)


def apply_staged(client, response):
    match = STAGING_RE.search(response.data.decode("utf-8", "replace"))
    assert match, "No staging/apply link found in the preview page"
    return client.post(f"/admin/import/{match.group(1)}/apply", follow_redirects=False)


def test_lines_import_end_to_end(client, app):
    login_admin(client)
    response = upload(client, "lines", "Name\nTestLine Alpha\nTestLine Beta")
    assert response.status_code == 200
    assert b"2 new" in response.data

    response = apply_staged(client, response)
    assert response.status_code == 302

    with app.app_context():
        for name in ("TestLine Alpha", "TestLine Beta"):
            row = db_module.query_one("SELECT * FROM lines WHERE name = ?", (name,))
            assert row is not None
            assert row["is_active"]


def test_lines_duplicate_in_file_flagged(client, app):
    login_admin(client)
    response = upload(client, "lines", "Name\nDup Line\nDup Line\nOther Line")
    body = response.data.decode("utf-8", "replace")
    # Dup Line planned once as NEW, its second occurrence flagged; Other Line NEW.
    assert "2 new" in body
    assert b"Duplicate Name" in response.data
    # Apply is not offered because there are errors.
    assert STAGING_RE.search(body) is None
    with app.app_context():
        count = db_module.query_one("SELECT COUNT(*) AS n FROM lines WHERE name = 'Dup Line'")
        assert count["n"] == 0


def test_unknown_designation_rejected(client, app):
    login_admin(client)
    response = upload(
        client,
        "employees",
        "Name,Emp ID,Designation,Reports To Name,Reports To Emp ID\nBad Person,BAD001,Chief Warden,,\n",
    )
    assert response.status_code == 200
    assert b"Designation" in response.data
    assert b"not recognised" in response.data


def test_employees_import_creates_logins_and_links_supervisor(client, app):
    login_admin(client)
    csv_text = (
        "Name,Emp ID,Designation,Reports To Name,Reports To Emp ID\n"
        "Zone Manager,ZM9001,Line Manager,,\n"
        "Zone Controller,ZC9002,Station Controller,Zone Manager,ZM9001\n"
    )
    response = upload(client, "employees", csv_text)
    assert response.status_code == 200
    assert b"2 new" in response.data

    response = apply_staged(client, response)
    assert response.status_code == 302

    with app.app_context():
        manager = db_module.query_one("SELECT * FROM users WHERE emp_id = 'ZM9001'")
        controller = db_module.query_one("SELECT * FROM users WHERE emp_id = 'ZC9002'")
        assert manager is not None and manager["role"] == "LINE_MANAGER"
        assert controller is not None and controller["role"] == "STATION_CONTROLLER"
        assert controller["superior_id"] == manager["id"]
        assert controller["is_active"] and manager["is_active"]

    # New employee can log in with the default password.
    client.get("/logout")
    response = client.post("/login", data={"username": "ZC9002", "password": "123"}, follow_redirects=False)
    assert response.status_code == 302


def test_supervisor_missing_from_file_and_db_flagged(client, app):
    login_admin(client)
    csv_text = (
        "Name,Emp ID,Designation,Reports To Name,Reports To Emp ID\n"
        "Lone Worker,LW9003,Station Controller,,NOPE999\n"
    )
    response = upload(client, "employees", csv_text)
    assert response.status_code == 200
    assert b"was not found" in response.data


def test_stations_import_and_missing_line_error(client, app):
    login_admin(client)
    with app.app_context():
        line = db_module.query_one("SELECT * FROM lines ORDER BY id LIMIT 1")
        line_name = line["name"]

    csv_text = f"Line Name,Station Name\n{line_name},IMPORT STATION X\nNoSuchLineZZZ,Orphan Station Y\n"
    response = upload(client, "stations", csv_text)
    body = response.data.decode("utf-8", "replace")
    assert b"was not found" in response.data  # second row errors on the unknown line

    # Upload a clean file (only the valid station) and apply it.
    clean = f"Line Name,Station Name\n{line_name},IMPORT STATION X\n"
    response = upload(client, "stations", clean)
    assert b"1 new" in response.data
    response = apply_staged(client, response)
    assert response.status_code == 302
    with app.app_context():
        row = db_module.query_one(
            "SELECT st.is_active FROM stations st JOIN lines l ON l.id = st.line_id WHERE st.name = 'IMPORT STATION X' AND l.name = ?",
            (line_name,),
        )
        assert row is not None and row["is_active"]


def test_deactivate_missing_flag(client, app):
    login_admin(client)
    response = upload(client, "lines", "Name\nOnlyLineZ", deactivate=True)
    assert response.status_code == 200
    assert b"Deactivate" in response.data
    assert b"OnlyLineZ" in response.data

    response = apply_staged(client, response)
    assert response.status_code == 302
    with app.app_context():
        kept = db_module.query_one("SELECT is_active FROM lines WHERE name = 'OnlyLineZ'")
        assert kept is not None and kept["is_active"]
        active_count = db_module.query_one("SELECT COUNT(*) AS n FROM lines WHERE is_active IS TRUE")
        assert active_count["n"] == 1


def test_template_download_and_parse_roundtrip(client):
    login_admin(client)
    response = client.get("/admin/import/template/employees")
    assert response.status_code == 200
    assert b"spreadsheetml" in response.data or response.data[:2] == b"PK"

    # A file built with the template headers parses correctly.
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Name", "Emp ID", "Designation", "Reports To Name", "Reports To Emp ID"])
    sheet.append(["Round Trip User", "RT0001", "Station Manager", "", ""])
    buffer = io.BytesIO()
    workbook.save(buffer)
    parsed = master_data.parse_file("employees", "t.xlsx", buffer.getvalue())
    assert parsed["error"] is None
    assert len(parsed["rows"]) == 1
    assert parsed["rows"][0]["values"]["emp_id"] == "RT0001"

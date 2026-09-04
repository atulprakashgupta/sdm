from datetime import date

import pytest

from app import db as db_module


def login(client, username, password="123"):
    return client.post("/login", data={"username": username, "password": password}, follow_redirects=False)


@pytest.fixture
def controller(app):
    with app.app_context():
        user = db_module.query_one(
            "SELECT * FROM users WHERE role = 'STATION_CONTROLLER' AND is_active IS TRUE ORDER BY id LIMIT 1"
        )
        assert user is not None, "No station controller in the seeded roster"
        return dict(user)


@pytest.fixture
def form_data():
    return {
        "foil_no": "FOIL-E2E-001",
        "memo_date": date.today().isoformat(),
        "line_id": "1",
        "station_id": "1",
        "contractor_id": "1",
        "staff_name": "Test Staff",
        "staff_emp_id": "TS001",
        "reason_id": "1",  # LATE_REPORTING needs no extra fields
        "remarks": "End-to-end test submission",
    }


def test_create_sdm_end_to_end(client, app, controller, form_data):
    assert login(client, controller["emp_id"]).status_code == 302

    response = client.post("/sdm/new", data=form_data, follow_redirects=False)
    assert response.status_code == 302
    assert "/sdm/" in response.headers["Location"]

    with app.app_context():
        sdm = db_module.query_one("SELECT * FROM sdm WHERE foil_no = ?", (form_data["foil_no"],))
        assert sdm is not None
        assert sdm["status"] == "PENDING"
        assert sdm["sdm_no"] and sdm["sdm_no"].startswith("SDM No. - ")
        assert sdm["current_step_index"] == 1
        assert sdm["current_role"] == "STATION_MANAGER"
        manager = db_module.query_one("SELECT * FROM users WHERE id = ?", (sdm["current_assignee_id"],))
        assert manager["role"] == "STATION_MANAGER"
        event = db_module.query_one("SELECT * FROM workflow_events WHERE sdm_id = ? AND event_type = 'CREATED'", (sdm["id"],))
        assert event is not None


def test_duplicate_foil_no_rejected(client, app, controller, form_data):
    login(client, controller["emp_id"])
    assert client.post("/sdm/new", data=form_data, follow_redirects=False).status_code == 302
    response = client.post("/sdm/new", data=form_data, follow_redirects=False)
    assert b"Foil No. has already been used" in response.data or response.status_code == 200
    with app.app_context():
        count = db_module.query_one("SELECT COUNT(*) AS n FROM sdm WHERE foil_no = ?", (form_data["foil_no"],))
        assert count["n"] == 1


def test_sdm_numbers_unique_across_submissions(client, app, controller, form_data):
    login(client, controller["emp_id"])
    numbers = []
    for i in range(3):
        data = dict(form_data)
        data["foil_no"] = f"FOIL-E2E-{i:03d}"
        response = client.post("/sdm/new", data=data, follow_redirects=False)
        assert response.status_code == 302
        with app.app_context():
            numbers.append(db_module.query_one("SELECT sdm_no FROM sdm WHERE foil_no = ?", (data["foil_no"],))["sdm_no"])
    assert len(set(numbers)) == 3


def test_forward_sdm_by_assignee(client, app, controller, form_data):
    login(client, controller["emp_id"])
    client.post("/sdm/new", data=form_data, follow_redirects=False)

    with app.app_context():
        sdm = db_module.query_one("SELECT * FROM sdm WHERE foil_no = ?", (form_data["foil_no"],))
        assignee = db_module.query_one("SELECT * FROM users WHERE id = ?", (sdm["current_assignee_id"],))

    # Log out and back in as the current assignee (station manager).
    client.get("/logout")
    assert login(client, assignee["emp_id"]).status_code == 302
    response = client.post(f"/sdm/{sdm['id']}/action", data={"action": "forward", "note": "forwarding"}, follow_redirects=False)
    assert response.status_code == 302

    with app.app_context():
        updated = db_module.query_one("SELECT * FROM sdm WHERE id = ?", (sdm["id"],))
        assert updated["current_step_index"] == 2
        assert updated["current_role"] == "LINE_MANAGER"
        manager = db_module.query_one("SELECT * FROM users WHERE id = ?", (updated["current_assignee_id"],))
        assert manager["role"] == "LINE_MANAGER"
        event = db_module.query_one("SELECT * FROM workflow_events WHERE sdm_id = ? AND event_type = 'FORWARDED'", (sdm["id"],))
        assert event is not None
        assert event["note"] == "forwarding"


def test_non_controller_cannot_create_sdm(client, app, form_data):
    # A CONCERNED_CELL user cannot open the new-SDM form.
    login(client, "cell")
    response = client.post("/sdm/new", data=form_data, follow_redirects=False)
    assert response.status_code == 302  # redirected to dashboard with warning
    with app.app_context():
        count = db_module.query_one("SELECT COUNT(*) AS n FROM sdm")
        assert count["n"] == 0

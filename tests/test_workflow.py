from datetime import datetime as real_datetime

import pytest

from app import db, workflow


# ---------------------------------------------------------------------------
# Permission helpers (pure functions, no DB needed)
# ---------------------------------------------------------------------------


def _user(**overrides):
    base = {
        "id": 1,
        "is_admin": 0,
        "role": "STATION_CONTROLLER",
        "name": "Test User",
        "emp_id": "T1",
    }
    base.update(overrides)
    return base


def _sdm(**overrides):
    base = {
        "id": 1,
        "status": "PENDING",
        "current_assignee_id": 2,
        "created_by": 1,
        "current_step_index": 1,
    }
    base.update(overrides)
    return base


def test_can_edit_creator_draft():
    sdm = _sdm(status="DRAFT", created_by=1)
    assert workflow.can_edit_sdm(_user(id=1), sdm)


def test_can_edit_admin_only_in_draft():
    sdm = _sdm(status="PENDING", created_by=2)
    assert not workflow.can_edit_sdm(_user(id=1, is_admin=1), sdm)
    assert workflow.can_edit_sdm(_user(id=1, is_admin=1), _sdm(status="DRAFT", created_by=2))


def test_can_edit_returned_assignee():
    sdm = _sdm(status="RETURNED", current_assignee_id=2, created_by=1)
    assert workflow.can_edit_sdm(_user(id=2), sdm)


def test_can_edit_creator_while_stage_one_pending():
    sdm = _sdm(status="PENDING", created_by=1, current_assignee_id=2, current_step_index=1)
    assert workflow.can_edit_sdm(_user(id=1), sdm)


def test_can_edit_creator_not_stage_one_pending():
    sdm = _sdm(status="PENDING", created_by=1, current_assignee_id=2, current_step_index=2)
    assert not workflow.can_edit_sdm(_user(id=1), sdm)


def test_can_edit_closed_and_cancelled():
    assert not workflow.can_edit_sdm(_user(id=1), _sdm(status="CLOSED", created_by=1))
    assert not workflow.can_edit_sdm(_user(id=1), _sdm(status="CANCELLED", created_by=1))


def test_can_edit_unrelated_user_denied():
    sdm = _sdm(status="PENDING", created_by=2, current_assignee_id=3)
    assert not workflow.can_edit_sdm(_user(id=1), sdm)


def test_can_act_on_sdm():
    sdm = _sdm(current_assignee_id=2)
    assert workflow.can_act_on_sdm(_user(id=2), sdm)
    assert not workflow.can_act_on_sdm(_user(id=1), sdm)
    assert not workflow.can_act_on_sdm(_user(id=2), _sdm(status="DRAFT", current_assignee_id=2))
    assert not workflow.can_act_on_sdm(_user(id=2), _sdm(status="CLOSED", current_assignee_id=2))


def test_can_cancel_only_allowed_roles():
    sdm = _sdm(current_assignee_id=2)
    for role in ("LINE_MANAGER", "DY_HOD", "HOD"):
        assert workflow.can_cancel_sdm(_user(id=2, role=role), sdm)
    assert not workflow.can_cancel_sdm(_user(id=2, role="STATION_CONTROLLER"), sdm)
    assert not workflow.can_cancel_sdm(_user(id=2, role="LINE_MANAGER"), _sdm(status="CLOSED", current_assignee_id=2))


def test_can_delete_attachment_only_draft_creator():
    assert workflow.can_delete_attachment(_user(id=1), _sdm(status="DRAFT", created_by=1))
    assert not workflow.can_delete_attachment(_user(id=2), _sdm(status="DRAFT", created_by=1))
    assert not workflow.can_delete_attachment(_user(id=1), _sdm(status="PENDING", created_by=1))


# ---------------------------------------------------------------------------
# SDM numbering (DB-backed)
# ---------------------------------------------------------------------------


def test_next_sdm_number_increments(db):
    first = workflow.next_sdm_number()
    second = workflow.next_sdm_number()
    assert first != second
    assert first.startswith("SDM No. - ")
    assert len(first.split("- ")[1]) == 12  # YYYYMMDD + 4 digits


def test_next_sdm_number_unique_across_many_calls(db):
    numbers = {workflow.next_sdm_number() for _ in range(25)}
    assert len(numbers) == 25


class _FrozenDate:
    iso = "2026-08-31"

    @classmethod
    def now(cls):
        return real_datetime.strptime(cls.iso, "%Y-%m-%d")


def test_next_sdm_number_rollover_resets_daily(db, monkeypatch):
    monkeypatch.setattr(workflow, "datetime", _FrozenDate)
    _FrozenDate.iso = "2026-08-31"
    assert workflow.next_sdm_number() == "SDM No. - 202608310001"
    assert workflow.next_sdm_number() == "SDM No. - 202608310002"

    _FrozenDate.iso = "2026-09-01"
    assert workflow.next_sdm_number() == "SDM No. - 202609010001"


# ---------------------------------------------------------------------------
# Forward assignee selection (DB-backed with controlled users)
# ---------------------------------------------------------------------------


@pytest.fixture
def routing_users(app):
    with app.app_context():
        db.execute(
            "INSERT INTO users (username, password_hash, name, emp_id, designation, role, is_admin, is_active, created_at)"
            " VALUES ('ctrltest', 'x', 'Controller', 'CTRL001', 'Station Controller', 'STATION_CONTROLLER', FALSE, TRUE, ?)",
            (db.now_iso(),),
        )
        db.execute(
            "INSERT INTO users (username, password_hash, name, emp_id, designation, role, is_admin, is_active, created_at)"
            " VALUES ('mgrtest', 'x', 'Manager', 'MGR001', 'Station Manager', 'STATION_MANAGER', FALSE, TRUE, ?)",
            (db.now_iso(),),
        )
        db.execute(
            "INSERT INTO users (username, password_hash, name, emp_id, designation, role, is_admin, is_active, created_at)"
            " VALUES ('mgr2test', 'x', 'Manager Two', 'MGR002', 'Station Manager', 'STATION_MANAGER', FALSE, TRUE, ?)",
            (db.now_iso(),),
        )
        db.execute(
            "INSERT INTO users (username, password_hash, name, emp_id, designation, role, is_admin, is_active, created_at)"
            " VALUES ('lmtest', 'x', 'Line Manager', 'LM001', 'Line Manager', 'LINE_MANAGER', FALSE, TRUE, ?)",
            (db.now_iso(),),
        )
        controller = db.query_one("SELECT * FROM users WHERE emp_id = 'CTRL001'")
        manager = db.query_one("SELECT * FROM users WHERE emp_id = 'MGR001'")
        db.execute("UPDATE users SET superior_id = ? WHERE id = ?", (manager["id"], controller["id"]))
        controller = db.query_one("SELECT * FROM users WHERE emp_id = 'CTRL001'")
        yield {"controller": dict(controller), "manager": dict(manager)}
        # Remove the test users so they do not leak into other assertions.
        for emp_id in ("CTRL001", "MGR001", "MGR002", "LM001"):
            db.execute("UPDATE users SET is_active = FALSE WHERE emp_id = ?", (emp_id,))


def test_select_forward_assignee_uses_superior(app, routing_users):
    with app.app_context():
        next_stage = workflow.stage_for_role("STATION_MANAGER")
        assignee = workflow.select_forward_assignee(routing_users["controller"], next_stage)
        assert assignee is not None
        assert assignee["id"] == routing_users["manager"]["id"]


def test_select_forward_assignee_explicit_selection(app, routing_users):
    with app.app_context():
        next_stage = workflow.stage_for_role("STATION_MANAGER")
        manager_two = db.query_one("SELECT * FROM users WHERE emp_id = 'MGR002'")
        assignee = workflow.select_forward_assignee(routing_users["controller"], next_stage, selected_assignee_id=manager_two["id"])
        assert assignee["id"] == manager_two["id"]


def test_select_forward_assignee_rejects_wrong_role(app, routing_users):
    with app.app_context():
        next_stage = workflow.stage_for_role("STATION_MANAGER")
        line_manager = db.query_one("SELECT * FROM users WHERE emp_id = 'LM001'")
        assignee = workflow.select_forward_assignee(routing_users["controller"], next_stage, selected_assignee_id=line_manager["id"])
        # Falls back to the superior rather than accepting the wrong role.
        assert assignee["id"] == routing_users["manager"]["id"]


def test_find_alternate_assignee_matching(app, routing_users):
    with app.app_context():
        assignee = workflow.find_alternate_assignee("Manager", "MGR001", "STATION_MANAGER")
        assert assignee["id"] == routing_users["manager"]["id"]


def test_find_alternate_assignee_name_mismatch(app, routing_users):
    with app.app_context():
        with pytest.raises(ValueError):
            workflow.find_alternate_assignee("Wrong Name", "MGR001", "STATION_MANAGER")


def test_find_alternate_assignee_unknown_emp(app, routing_users):
    with app.app_context():
        with pytest.raises(ValueError):
            workflow.find_alternate_assignee("Manager", "NOPE999", "STATION_MANAGER")

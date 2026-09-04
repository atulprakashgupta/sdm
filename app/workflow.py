from datetime import datetime

from . import db


STAGE_ROLES = [
    "STATION_CONTROLLER",
    "STATION_MANAGER",
    "LINE_MANAGER",
    "DY_HOD",
    "CONCERNED_CELL",
]

FINAL_STATUSES = {"CANCELLED", "CLOSED", "COMPLETED", "REJECTED"}
CANCELLATION_ROLES = {"LINE_MANAGER", "DY_HOD", "HOD"}

NEXT_ROLE_MAP = {
    "STATION_CONTROLLER": "STATION_MANAGER",
    "STATION_MANAGER": "LINE_MANAGER",
    "LINE_MANAGER": "DY_HOD",
    "DY_HOD": "CONCERNED_CELL",
}


def next_sdm_number():
    """Return the next organization-wide SDM number for today.

    The counter is updated atomically in one UPDATE ... RETURNING statement so
    concurrent submissions cannot observe the same value. The statement always
    matches the row (seeded at init), so both SQLite and PostgreSQL serialize
    competing writers on the row lock. A date change resets the counter to 1;
    the upsert fallback covers a missing row.
    """
    database = db.get_db()
    date_str = datetime.now().strftime("%Y%m%d")
    row = database.execute(
        """
        UPDATE counters
        SET value = CASE WHEN date_str = ? THEN value + 1 ELSE 1 END,
            date_str = ?
        WHERE name = 'SDM'
        RETURNING value
        """,
        (date_str, date_str),
    ).fetchone()
    if row is None:
        row = database.execute(
            """
            INSERT INTO counters (name, date_str, value)
            VALUES ('SDM', ?, 1)
            ON CONFLICT (name) DO UPDATE
              SET value = CASE WHEN counters.date_str = ? THEN counters.value + 1 ELSE 1 END,
                  date_str = excluded.date_str
            RETURNING value
            """,
            (date_str, date_str),
        ).fetchone()
    return f"SDM No. - {date_str}{int(row['value']):04d}"


def enabled_stages():
    return db.query_all(
        """
        SELECT ws.*, u.name AS default_user_name
        FROM workflow_stages ws
        LEFT JOIN users u ON u.id = ws.default_user_id
        WHERE ws.is_enabled IS TRUE
        ORDER BY ws.step_index
        """
    )


def stage_for_index(step_index):
    return db.query_one("SELECT * FROM workflow_stages WHERE step_index = ? AND is_enabled IS TRUE", (step_index,))


def stage_for_role(role):
    return db.query_one("SELECT * FROM workflow_stages WHERE role = ? AND is_enabled IS TRUE", (role,))


def next_stage(current_step_index):
    return db.query_one(
        """
        SELECT * FROM workflow_stages
        WHERE step_index > ? AND is_enabled IS TRUE
        ORDER BY step_index
        LIMIT 1
        """,
        (current_step_index,),
    )


def previous_stage(current_step_index):
    return db.query_one(
        """
        SELECT * FROM workflow_stages
        WHERE step_index < ? AND is_enabled IS TRUE
        ORDER BY step_index DESC
        LIMIT 1
        """,
        (current_step_index,),
    )


def users_for_role(role):
    return db.query_all(
        "SELECT * FROM users WHERE role = ? AND is_active IS TRUE ORDER BY name",
        (role,),
    )


def default_assignee_for_stage(stage):
    if stage["default_user_id"]:
        user = db.query_one("SELECT * FROM users WHERE id = ? AND is_active IS TRUE", (stage["default_user_id"],))
        if user:
            return user
    users = users_for_role(stage["role"])
    return users[0] if users else None


def select_forward_assignee(current_user, next_stage, selected_assignee_id=None):
    if not next_stage:
        return None

    if selected_assignee_id:
        assignee = db.query_one("SELECT * FROM users WHERE id = ? AND is_active IS TRUE", (selected_assignee_id,))
        if assignee and assignee["role"] == next_stage["role"]:
            return assignee

    if current_user and current_user["superior_id"]:
        assignee = db.query_one("SELECT * FROM users WHERE id = ? AND is_active IS TRUE", (current_user["superior_id"],))
        if assignee and assignee["role"] == next_stage["role"]:
            return assignee

    return default_assignee_for_stage(next_stage)


def find_alternate_assignee(name, emp_id, role):
    name = (name or "").strip()
    emp_id = (emp_id or "").strip()
    if not name and not emp_id:
        return None
    if not name or not emp_id:
        raise ValueError("Enter both alternate officer name and employment number.")

    assignee = db.query_one(
        """
        SELECT * FROM users
        WHERE emp_id = ? AND role = ? AND is_active IS TRUE
        """,
        (emp_id, role),
    )
    if not assignee:
        raise ValueError("No active alternate officer found for the next stage.")
    if assignee["name"].strip().lower() != name.lower():
        raise ValueError("Alternate officer name and employment number do not match.")
    return assignee


def can_view_sdm(user, sdm):
    return bool(user and sdm)


def can_edit_sdm(user, sdm):
    if not user or not sdm:
        return False
    if sdm["status"] in FINAL_STATUSES:
        return False
    if user["is_admin"] and sdm["status"] == "DRAFT":
        return True
    if sdm["current_assignee_id"] == user["id"] and sdm["status"] == "RETURNED":
        return True
    if sdm["created_by"] == user["id"] and sdm["status"] in {"DRAFT", "RETURNED"}:
        return True
    return (
        sdm["created_by"] == user["id"]
        and sdm["current_step_index"] == 1
        and sdm["status"] in {"PENDING", "IN_REVIEW"}
    )


def can_delete_attachment(user, sdm):
    return bool(user and sdm and sdm["status"] == "DRAFT" and sdm["created_by"] == user["id"])


def can_act_on_sdm(user, sdm):
    return bool(user and sdm and sdm["current_assignee_id"] == user["id"] and sdm["status"] not in FINAL_STATUSES and sdm["status"] != "DRAFT")


def can_cancel_sdm(user, sdm):
    return can_act_on_sdm(user, sdm) and user["role"] in CANCELLATION_ROLES


def add_event(sdm_id, event_type, actor_id, from_step_index=None, to_step_index=None, from_role=None, to_role=None, assigned_to_id=None, note=None):
    db.get_db().execute(
        """
        INSERT INTO workflow_events
        (sdm_id, event_type, from_step_index, to_step_index, from_role, to_role, actor_id, assigned_to_id, note, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            sdm_id,
            event_type,
            from_step_index,
            to_step_index,
            from_role,
            to_role,
            actor_id,
            assigned_to_id,
            note,
            db.now_iso(),
        ),
    )


def latest_forward_event_to_step(sdm_id, step_index):
    return db.query_one(
        """
        SELECT * FROM workflow_events
        WHERE sdm_id = ? AND to_step_index = ? AND event_type = 'CREATED'
        ORDER BY id DESC
        LIMIT 1
        """,
        (sdm_id, step_index),
    )

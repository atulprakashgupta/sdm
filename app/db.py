import csv
import sqlite3
from datetime import datetime
from pathlib import Path

import click
from flask import current_app, g
from werkzeug.security import generate_password_hash

from . import contractor_data, station_seed


BASE_DIR = Path(__file__).resolve().parent.parent
EMPLOYEE_CSV_PATH = BASE_DIR / "employee_list.csv"

DESIGNATION_ROLE_MAP = {
    "Dy. HOD": "DY_HOD",
    "Line Manager": "LINE_MANAGER",
    "Station Manager": "STATION_MANAGER",
    "Station Controller": "STATION_CONTROLLER",
}

ROLE_LABEL_MAP = {
    "STATION_CONTROLLER": "Station Controller",
    "STATION_MANAGER": "Station Manager",
    "LINE_MANAGER": "Line Manager",
    "DY_HOD": "Dy. HOD",
    "HOD": "HOD",
    "CONCERNED_CELL": "Concerned Cell",
}

SUPERVISOR_ROLE_MAP = {
    "STATION_CONTROLLER": "STATION_MANAGER",
    "STATION_MANAGER": "LINE_MANAGER",
    "LINE_MANAGER": "DY_HOD",
    "DY_HOD": "HOD",
}


def now_iso():
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def employee_role_from_designation(designation):
    return DESIGNATION_ROLE_MAP.get(designation, "STATION_CONTROLLER")


def role_label(role):
    return ROLE_LABEL_MAP.get(role, role.replace("_", " ").title())


def supervisor_role_for(role):
    return SUPERVISOR_ROLE_MAP.get(role)


def load_employee_roster(csv_path=EMPLOYEE_CSV_PATH):
    employees = []
    if not csv_path.exists():
        return employees

    with csv_path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        next(reader, None)
        for row in reader:
            if len(row) < 5:
                continue
            name, emp_no, designation, reports_to_name, reports_to_emp_no = [cell.strip() for cell in row[:5]]
            if not emp_no:
                continue
            employees.append(
                {
                    "name": name,
                    "emp_no": emp_no,
                    "designation": designation,
                    "role": employee_role_from_designation(designation),
                    "reports_to_name": reports_to_name,
                    "reports_to_emp_no": reports_to_emp_no,
                }
            )
    return employees


def sync_employee_roster(database, csv_path=EMPLOYEE_CSV_PATH, password="123"):
    employees = load_employee_roster(csv_path)
    if not employees:
        return {}

    timestamp = now_iso()
    password_hash = generate_password_hash(password)
    employee_ids = {}
    existing_rows = {}

    for employee in employees:
        row = database.execute("SELECT id, line_id, created_at FROM users WHERE emp_id = ?", (employee["emp_no"],)).fetchone()
        existing_rows[employee["emp_no"]] = row
        if row:
            database.execute(
                """
                UPDATE users
                SET username = ?, password_hash = ?, name = ?, designation = ?, role = ?, is_active = TRUE
                WHERE emp_id = ?
                """,
                (
                    employee["emp_no"],
                    password_hash,
                    employee["name"],
                    employee["designation"],
                    employee["role"],
                    employee["emp_no"],
                ),
            )
            employee_ids[employee["emp_no"]] = row["id"]
            continue

        cursor = database.execute(
            """
            INSERT INTO users
            (username, password_hash, name, emp_id, designation, role, line_id, superior_id, is_admin, is_active, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (
                employee["emp_no"],
                password_hash,
                employee["name"],
                employee["emp_no"],
                employee["designation"],
                employee["role"],
                None,
                None,
                False,
                True,
                timestamp,
            ),
        )
        employee_ids[employee["emp_no"]] = cursor.fetchone()["id"]

    # Create placeholder supervisors that are referenced but missing from the roster.
    pending_placeholders = {}
    for employee in employees:
        supervisor_emp_no = employee["reports_to_emp_no"]
        supervisor_name = employee["reports_to_name"]
        if not supervisor_emp_no or supervisor_emp_no in employee_ids:
            continue
        supervisor_role = supervisor_role_for(employee["role"])
        if not supervisor_role or supervisor_emp_no in pending_placeholders:
            continue
        pending_placeholders[supervisor_emp_no] = {
            "name": supervisor_name or supervisor_emp_no,
            "emp_no": supervisor_emp_no,
            "designation": role_label(supervisor_role),
            "role": supervisor_role,
        }

    for supervisor in pending_placeholders.values():
        existing_supervisor = database.execute(
            "SELECT id, username, line_id, created_at FROM users WHERE emp_id = ? ORDER BY id LIMIT 1",
            (supervisor["emp_no"],),
        ).fetchone()
        if not existing_supervisor:
            existing_supervisor = database.execute(
                "SELECT id, username, line_id, created_at FROM users WHERE role = ? AND is_admin IS FALSE ORDER BY id LIMIT 1",
                (supervisor["role"],),
            ).fetchone()
        if existing_supervisor:
            database.execute(
                """
                UPDATE users
                SET username = ?, password_hash = ?, name = ?, emp_id = ?, designation = ?, role = ?, is_active = TRUE
                WHERE id = ?
                """,
                (
                    existing_supervisor["username"],
                    password_hash,
                    supervisor["name"],
                    supervisor["emp_no"],
                    supervisor["designation"],
                    supervisor["role"],
                    existing_supervisor["id"],
                ),
            )
            employee_ids[supervisor["emp_no"]] = existing_supervisor["id"]
        else:
            cursor = database.execute(
                """
                INSERT INTO users
                (username, password_hash, name, emp_id, designation, role, line_id, superior_id, is_admin, is_active, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id
                """,
                (
                    supervisor["emp_no"],
                    password_hash,
                    supervisor["name"],
                    supervisor["emp_no"],
                    supervisor["designation"],
                    supervisor["role"],
                    None,
                    None,
                    False,
                    True,
                    timestamp,
                ),
            )
            employee_ids[supervisor["emp_no"]] = cursor.fetchone()["id"]

    for employee in employees:
        supervisor_id = None
        if employee["reports_to_emp_no"]:
            supervisor_id = employee_ids.get(employee["reports_to_emp_no"])
        elif employee["reports_to_name"]:
            supervisor = database.execute(
                "SELECT id FROM users WHERE name = ? AND is_active IS TRUE ORDER BY id LIMIT 1",
                (employee["reports_to_name"],),
            ).fetchone()
            if supervisor:
                supervisor_id = supervisor["id"]

        line_id = None
        if existing_rows.get(employee["emp_no"]):
            line_id = existing_rows[employee["emp_no"]]["line_id"]
        if line_id is None and supervisor_id:
            supervisor_row = database.execute("SELECT line_id FROM users WHERE id = ?", (supervisor_id,)).fetchone()
            if supervisor_row:
                line_id = supervisor_row["line_id"]

        database.execute(
            "UPDATE users SET superior_id = ?, line_id = COALESCE(line_id, ?) WHERE emp_id = ?",
            (supervisor_id, line_id, employee["emp_no"]),
        )

    return employee_ids


# ---------------------------------------------------------------------------
# Backend-agnostic connection layer.
#
# Development uses SQLite (default). Production can point DATABASE_URL at a
# PostgreSQL database; psycopg (v3) must then be installed. Queries are written
# in a portable subset of SQL: boolean columns are compared with IS TRUE /
# IS FALSE (supported by SQLite >= 3.23 and PostgreSQL), and the only dialect
# difference left is the placeholder style (? vs %s), which _BackendConnection
# converts for PostgreSQL. Inserts that need the new row id use RETURNING.
# ---------------------------------------------------------------------------


class _BackendConnection:
    """Thin wrapper hiding the placeholder difference between SQLite and PostgreSQL."""

    def __init__(self, raw, backend):
        self._raw = raw
        self._backend = backend

    def _convert_sql(self, sql):
        if self._backend == "postgres" and "?" in sql:
            return sql.replace("?", "%s")
        return sql

    def execute(self, sql, params=()):
        return self._raw.execute(self._convert_sql(sql), params)

    def executemany(self, sql, params_seq):
        return self._raw.executemany(self._convert_sql(sql), params_seq)

    def executescript(self, script):
        if self._backend == "sqlite":
            return self._raw.executescript(script)
        # psycopg has no executescript; run each statement individually.
        for statement in script.split(";"):
            statement = statement.strip()
            if statement:
                self._raw.execute(self._convert_sql(statement))
        return None

    def __getattr__(self, name):
        return getattr(self._raw, name)


def get_backend():
    return "postgres" if current_app.config.get("DATABASE_URL") else "sqlite"


def connect():
    if get_backend() == "postgres":
        import psycopg

        raw = psycopg.connect(current_app.config["DATABASE_URL"], row_factory=psycopg.rows.dict_row)
        return _BackendConnection(raw, "postgres")

    raw = sqlite3.connect(current_app.config["DATABASE"])
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA foreign_keys = ON")
    return _BackendConnection(raw, "sqlite")


def integrity_error():
    if get_backend() == "postgres":
        import psycopg

        return psycopg.errors.UniqueViolation
    return sqlite3.IntegrityError


def get_db():
    if "db" not in g:
        g.db = connect()
    return g.db


def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def query_one(sql, params=()):
    return get_db().execute(sql, params).fetchone()


def query_all(sql, params=()):
    return get_db().execute(sql, params).fetchall()


def execute(sql, params=()):
    database = get_db()
    cursor = database.execute(sql, params)
    database.commit()
    return cursor


SQLITE_SCHEMA = """
DROP TABLE IF EXISTS password_reset_tokens;
DROP TABLE IF EXISTS workflow_events;
DROP TABLE IF EXISTS attachments;
DROP TABLE IF EXISTS sdm;
DROP TABLE IF EXISTS workflow_stages;
DROP TABLE IF EXISTS contractor_staff;
DROP TABLE IF EXISTS reasons;
DROP TABLE IF EXISTS contractors;
DROP TABLE IF EXISTS stations;
DROP TABLE IF EXISTS lines;
DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS counters;

CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    name TEXT NOT NULL,
    emp_id TEXT NOT NULL UNIQUE,
    designation TEXT NOT NULL,
    role TEXT NOT NULL,
    line_id INTEGER,
    superior_id INTEGER,
    is_admin INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE stations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    line_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    UNIQUE (line_id, name)
);

CREATE TABLE contractors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE contractor_staff (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contractor_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    emp_id TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (contractor_id) REFERENCES contractors (id),
    UNIQUE (contractor_id, emp_id)
);

CREATE TABLE reasons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    needs_public_complaint INTEGER NOT NULL DEFAULT 0,
    needs_overcharging_status INTEGER NOT NULL DEFAULT 0,
    needs_inspection_details INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE workflow_stages (
    step_index INTEGER PRIMARY KEY,
    role TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL,
    default_user_id INTEGER,
    is_enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE counters (
    name TEXT PRIMARY KEY,
    date_str TEXT NOT NULL,
    value INTEGER NOT NULL
);

CREATE TABLE sdm (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sdm_no TEXT UNIQUE,
    foil_no TEXT NOT NULL UNIQUE,
    memo_date TEXT NOT NULL,
    line_id INTEGER NOT NULL,
    station_id INTEGER NOT NULL,
    contractor_id INTEGER NOT NULL,
    staff_name TEXT NOT NULL,
    staff_emp_id TEXT NOT NULL,
    reason_id INTEGER NOT NULL,
    reason_payload TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    current_step_index INTEGER NOT NULL DEFAULT 0,
    current_role TEXT NOT NULL DEFAULT 'STATION_CONTROLLER',
    current_assignee_id INTEGER NOT NULL,
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    submitted_at TEXT,
    remarks TEXT,
    penalty_amount REAL,
    penalty_modified_by INTEGER,
    penalty_modified_at TEXT,
    cancelled_at TEXT,
    closed_at TEXT
);

CREATE TABLE attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sdm_id INTEGER NOT NULL,
    stored_filename TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    content_type TEXT,
    size_bytes INTEGER NOT NULL,
    uploaded_by INTEGER NOT NULL,
    uploaded_at TEXT NOT NULL,
    deleted_at TEXT
);

CREATE TABLE workflow_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sdm_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    from_step_index INTEGER,
    to_step_index INTEGER,
    from_role TEXT,
    to_role TEXT,
    actor_id INTEGER NOT NULL,
    assigned_to_id INTEGER,
    note TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE password_reset_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_sdm_current_work ON sdm (current_assignee_id, status, created_at);
CREATE INDEX idx_sdm_creator_status ON sdm (created_by, status, created_at);
CREATE INDEX idx_sdm_reports_date ON sdm (memo_date, status);
CREATE INDEX idx_sdm_reports_master ON sdm (line_id, station_id, contractor_id, reason_id);
CREATE INDEX idx_attachments_sdm ON attachments (sdm_id, deleted_at);
CREATE INDEX idx_workflow_events_sdm ON workflow_events (sdm_id, id);
CREATE INDEX idx_reset_tokens_user ON password_reset_tokens (user_id);
"""


def init_db():
    database = get_db()
    if get_backend() == "sqlite":
        database.execute("PRAGMA foreign_keys = OFF")
        database.executescript(SQLITE_SCHEMA)
        database.execute("PRAGMA foreign_keys = ON")
    else:
        schema_path = BASE_DIR / "deployment" / "postgresql_schema.sql"
        database.executescript(schema_path.read_text(encoding="utf-8"))
    seed_db(database)
    database.commit()


def seed_db(database):
    timestamp = now_iso()

    # Admin users
    users = [
        ("admin", "123", "System Admin", "ADM001", "Administrator", "ADMIN", True),
        ("hod", "123", "HOD", "HOD001", "HOD", "HOD", False),
        ("cell", "123", "Concerned Cell Officer", "CELL001", "Concerned Cell", "CONCERNED_CELL", False),
    ]

    for username, password, name, emp_id, designation, role, is_admin in users:
        database.execute(
            """
            INSERT INTO users
            (username, password_hash, name, emp_id, designation, role, is_admin, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (username, generate_password_hash(password), name, emp_id, designation, role, is_admin, timestamp),
        )

    sync_employee_roster(database)

    if not database.execute("SELECT 1 FROM users WHERE role = 'CONCERNED_CELL' LIMIT 1").fetchone():
        database.execute(
            """
            INSERT INTO users
            (username, password_hash, name, emp_id, designation, role, is_admin, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("cell", generate_password_hash("123"), "Concerned Cell", "CELL001", "Concerned Cell", "CONCERNED_CELL", False, timestamp),
        )

    # Seed stations with line_id mapping
    line_id_map = {}
    for idx, line_name in enumerate(station_seed.LINE_NAMES):
        database.execute("INSERT INTO lines (name) VALUES (?)", (line_name,))
        line_id_map[line_name] = idx + 1

    for line_name, station_name, station_code in station_seed.STATIONS:
        line_id = line_id_map.get(line_name)
        if line_id:
            # Include station code in name to handle duplicates within same line
            full_name = f"{station_name} ({station_code})" if station_code else station_name
            database.execute("INSERT INTO stations (line_id, name) VALUES (?, ?)", (line_id, full_name))

    # Seed contractors
    for contractor_name in contractor_data.CONTRACTORS:
        database.execute("INSERT INTO contractors (name) VALUES (?)", (contractor_name,))

    reasons = [
        ("LATE_REPORTING", "Late Reporting", 0, 0, 0),
        ("POOR_DRESS_CODE", "Poor Dress code & Failure to follow instructions", 0, 0, 0),
        ("MISBEHAVIOR", "Misbehavior with customer/staff", 0, 0, 0),
        ("PUBLIC_COMPLAINT", "Public Complaint", 1, 0, 0),
        ("OVERCHARGING", "Overcharging", 0, 1, 0),
        ("INSPECTION_CASH", "TOM/CC/TVM Inspection/Cash checking details", 0, 0, 1),
        ("OTHER", "Other", 0, 0, 0),
    ]
    for reason in reasons:
        database.execute(
            """
            INSERT INTO reasons
            (code, name, needs_public_complaint, needs_overcharging_status, needs_inspection_details)
            VALUES (?, ?, ?, ?, ?)
            """,
            reason,
        )

    # Set default users for workflow stages (use first created user of each role)
    default_users = {}
    for role in ["STATION_CONTROLLER", "STATION_MANAGER", "LINE_MANAGER", "DY_HOD", "CONCERNED_CELL"]:
        user = database.execute("SELECT id FROM users WHERE role = ? LIMIT 1", (role,)).fetchone()
        if user:
            default_users[role] = user["id"]

    stages = [
        (0, "STATION_CONTROLLER", "Station Controller"),
        (1, "STATION_MANAGER", "Station Manager"),
        (2, "LINE_MANAGER", "Line Manager"),
        (3, "DY_HOD", "Dy. HOD"),
        (4, "CONCERNED_CELL", "Concerned Cell"),
    ]
    for step_index, role, label in stages:
        database.execute(
            """
            INSERT INTO workflow_stages (step_index, role, label, default_user_id)
            VALUES (?, ?, ?, ?)
            """,
            (step_index, role, label, default_users.get(role)),
        )

    database.execute("INSERT INTO counters (name, date_str, value) VALUES (?, ?, ?)", ("SDM", datetime.now().strftime("%Y%m%d"), 0))


@click.command("init-db")
def init_db_command():
    init_db()
    click.echo("Initialized the SDM database.")


def init_app(app):
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)

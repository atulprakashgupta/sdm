import sqlite3
from pathlib import Path
from datetime import datetime

from werkzeug.security import generate_password_hash

from app.db import EMPLOYEE_CSV_PATH, load_employee_roster, role_label, supervisor_role_for


def main():
    database_path = Path("instance/sdm.sqlite")
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row

    employees = load_employee_roster(EMPLOYEE_CSV_PATH)
    timestamp = datetime.now().replace(microsecond=0).isoformat(sep=" ")
    password_hash = generate_password_hash("123")

    employee_ids = {}
    existing_rows = {}

    for employee in employees:
        row = connection.execute("SELECT id, line_id FROM users WHERE emp_id = ?", (employee["emp_no"],)).fetchone()
        existing_rows[employee["emp_no"]] = row
        if row:
            connection.execute(
                """
                UPDATE users
                SET username = ?, password_hash = ?, name = ?, designation = ?, role = ?, is_active = 1
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
        else:
            cursor = connection.execute(
                """
                INSERT INTO users
                (username, password_hash, name, emp_id, designation, role, line_id, superior_id, is_admin, is_active, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    0,
                    1,
                    timestamp,
                ),
            )
            employee_ids[employee["emp_no"]] = cursor.lastrowid

    for employee in employees:
        supervisor_emp_no = employee["reports_to_emp_no"]
        if supervisor_emp_no and supervisor_emp_no not in employee_ids:
            supervisor_role = supervisor_role_for(employee["role"])
            if supervisor_role:
                existing_supervisor = connection.execute(
                    "SELECT id, username FROM users WHERE emp_id = ? ORDER BY id LIMIT 1",
                    (supervisor_emp_no,),
                ).fetchone()
                if not existing_supervisor:
                    existing_supervisor = connection.execute(
                        "SELECT id, username FROM users WHERE role = ? AND is_admin = 0 ORDER BY id LIMIT 1",
                        (supervisor_role,),
                    ).fetchone()
                if existing_supervisor:
                    connection.execute(
                        """
                        UPDATE users
                        SET username = ?, password_hash = ?, name = ?, emp_id = ?, designation = ?, role = ?, is_active = 1
                        WHERE id = ?
                        """,
                        (
                            existing_supervisor["username"],
                            password_hash,
                            employee["reports_to_name"] or supervisor_emp_no,
                            supervisor_emp_no,
                            role_label(supervisor_role),
                            supervisor_role,
                            existing_supervisor["id"],
                        ),
                    )
                    employee_ids[supervisor_emp_no] = existing_supervisor["id"]
                else:
                    connection.execute(
                        """
                        INSERT INTO users
                        (username, password_hash, name, emp_id, designation, role, is_admin, is_active, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            supervisor_emp_no,
                            password_hash,
                            employee["reports_to_name"] or supervisor_emp_no,
                            supervisor_emp_no,
                            role_label(supervisor_role),
                            supervisor_role,
                            0,
                            1,
                            timestamp,
                        ),
                    )
                    supervisor_row = connection.execute("SELECT id FROM users WHERE emp_id = ?", (supervisor_emp_no,)).fetchone()
                    if supervisor_row:
                        employee_ids[supervisor_emp_no] = supervisor_row["id"]

    for employee in employees:
        supervisor_id = None
        if employee["reports_to_emp_no"]:
            supervisor_id = employee_ids.get(employee["reports_to_emp_no"])
        elif employee["reports_to_name"]:
            supervisor = connection.execute(
                "SELECT id FROM users WHERE name = ? AND is_active = 1 ORDER BY id LIMIT 1",
                (employee["reports_to_name"],),
            ).fetchone()
            if supervisor:
                supervisor_id = supervisor["id"]

        existing_row = existing_rows.get(employee["emp_no"])
        line_id = existing_row["line_id"] if existing_row else None
        if line_id is None and supervisor_id:
            supervisor = connection.execute("SELECT line_id FROM users WHERE id = ?", (supervisor_id,)).fetchone()
            if supervisor:
                line_id = supervisor["line_id"]

        connection.execute(
            "UPDATE users SET superior_id = ?, line_id = COALESCE(line_id, ?) WHERE emp_id = ?",
            (supervisor_id, line_id, employee["emp_no"]),
        )

    connection.commit()
    connection.close()
    print(f"Synced {len(employees)} employee records from {EMPLOYEE_CSV_PATH.name}.")


if __name__ == "__main__":
    main()

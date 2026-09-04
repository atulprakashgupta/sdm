import re

import pytest


def login(client, username, password):
    return client.post("/login", data={"username": username, "password": password}, follow_redirects=False)


def test_login_success(client):
    response = login(client, "admin", "123")
    assert response.status_code == 302
    assert "/login" not in response.headers["Location"]


def test_login_failure(client):
    response = login(client, "admin", "wrong-password")
    assert response.status_code == 200
    assert b"Invalid username or password" in response.data


def test_login_requires_active_account(client):
    response = login(client, "cell", "123")
    assert response.status_code == 302


def test_change_password_flow(client, app):
    login(client, "admin", "123")
    response = client.post(
        "/change-password",
        data={"current_password": "123", "new_password": "newpass456", "confirm_password": "newpass456"},
        follow_redirects=False,
    )
    assert response.status_code == 302

    # Old password no longer works, new one does.
    client.get("/logout")
    assert login(client, "admin", "123").status_code == 200
    assert login(client, "admin", "newpass456").status_code == 302


def test_change_password_requires_current_password(client):
    login(client, "admin", "123")
    response = client.post(
        "/change-password",
        data={"current_password": "notright", "new_password": "newpass456", "confirm_password": "newpass456"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert b"Current password is incorrect" in response.data


def test_change_password_mismatch(client):
    login(client, "admin", "123")
    response = client.post(
        "/change-password",
        data={"current_password": "123", "new_password": "newpass456", "confirm_password": "different7"},
        follow_redirects=False,
    )
    assert b"do not match" in response.data


def test_change_password_requires_login(client):
    response = client.get("/change-password", follow_redirects=False)
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def _request_reset_link(client, emp_id="CELL001"):
    response = client.post("/forgot-password", data={"emp_id": emp_id}, follow_redirects=False)
    match = re.search(rb'href="([^"]+/reset-password/([A-Za-z0-9_\-]+))"', response.data)
    return response, match


def test_forgot_password_unknown_emp_id(client):
    response = client.post("/forgot-password", data={"emp_id": "NOPE000"}, follow_redirects=False)
    assert response.status_code == 200
    assert b"No active account was found" in response.data
    assert b"reset-password/" not in response.data


def test_forgot_password_generates_link(client, app):
    response, match = _request_reset_link(client, "CELL001")
    assert match is not None, response.data
    with app.app_context():
        from app import db

        row = db.query_one("SELECT * FROM password_reset_tokens")
        assert row is not None
        assert row["expires_at"] > db.now_iso()


def test_reset_password_flow(client, app):
    response, match = _request_reset_link(client, "CELL001")
    assert match is not None, response.data
    token = match.group(2).decode()

    # Set the new password using the link.
    response = client.post(
        f"/reset-password/{token}",
        data={"new_password": "brandnew99", "confirm_password": "brandnew99"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]

    # Token is single-use: a second attempt is rejected.
    response = client.post(
        f"/reset-password/{token}",
        data={"new_password": "another12", "confirm_password": "another12"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert b"This reset link is invalid or has expired" in response.data or "/forgot-password" in response.headers["Location"]

    # New password works, old one does not.
    assert login(client, "cell", "123").status_code == 200
    assert login(client, "cell", "brandnew99").status_code == 302


def test_reset_password_invalid_token(client):
    response = client.post(
        "/reset-password/not-a-real-token",
        data={"new_password": "brandnew99", "confirm_password": "brandnew99"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "/forgot-password" in response.headers["Location"]


def test_reset_password_link_expired(client, app, monkeypatch):
    response, match = _request_reset_link(client, "CELL001")
    assert match is not None, response.data
    token = match.group(2).decode()

    with app.app_context():
        from app import db

        db.execute("UPDATE password_reset_tokens SET expires_at = '2000-01-01 00:00:00'")

    response = client.post(
        f"/reset-password/{token}",
        data={"new_password": "brandnew99", "confirm_password": "brandnew99"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "/forgot-password" in response.headers["Location"]


def test_forgot_password_page_loads(client):
    response = client.get("/forgot-password")
    assert response.status_code == 200
    assert b"Employment No." in response.data

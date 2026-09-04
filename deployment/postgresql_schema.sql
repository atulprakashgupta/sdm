-- PostgreSQL schema for the SDM production build.
-- Mirrors the SQLite development schema (app/db.py). The app's init-db command
-- applies this file when SDM_DATABASE_URL is set, so keep the two in sync.

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
    id BIGSERIAL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    name TEXT NOT NULL,
    emp_id TEXT NOT NULL UNIQUE,
    designation TEXT NOT NULL,
    role TEXT NOT NULL,
    line_id BIGINT,
    superior_id BIGINT,
    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL
);

CREATE TABLE lines (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE stations (
    id BIGSERIAL PRIMARY KEY,
    line_id BIGINT NOT NULL REFERENCES lines(id),
    name TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (line_id, name)
);

CREATE TABLE contractors (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE contractor_staff (
    id BIGSERIAL PRIMARY KEY,
    contractor_id BIGINT NOT NULL REFERENCES contractors(id),
    name TEXT NOT NULL,
    emp_id TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (contractor_id, emp_id)
);

CREATE TABLE reasons (
    id BIGSERIAL PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    needs_public_complaint BOOLEAN NOT NULL DEFAULT FALSE,
    needs_overcharging_status BOOLEAN NOT NULL DEFAULT FALSE,
    needs_inspection_details BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE workflow_stages (
    step_index INTEGER PRIMARY KEY,
    role TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL,
    default_user_id BIGINT REFERENCES users(id),
    is_enabled BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE counters (
    name TEXT PRIMARY KEY,
    date_str TEXT NOT NULL,
    value INTEGER NOT NULL
);

CREATE TABLE sdm (
    id BIGSERIAL PRIMARY KEY,
    sdm_no TEXT UNIQUE,
    foil_no TEXT NOT NULL UNIQUE,
    memo_date DATE NOT NULL,
    line_id BIGINT NOT NULL REFERENCES lines(id),
    station_id BIGINT NOT NULL REFERENCES stations(id),
    contractor_id BIGINT NOT NULL REFERENCES contractors(id),
    staff_name TEXT NOT NULL,
    staff_emp_id TEXT NOT NULL,
    reason_id BIGINT NOT NULL REFERENCES reasons(id),
    reason_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL,
    current_step_index INTEGER NOT NULL DEFAULT 0,
    current_role TEXT NOT NULL DEFAULT 'STATION_CONTROLLER',
    current_assignee_id BIGINT NOT NULL REFERENCES users(id),
    created_by BIGINT NOT NULL REFERENCES users(id),
    created_at TIMESTAMP NOT NULL,
    submitted_at TIMESTAMP,
    remarks TEXT,
    penalty_amount REAL,
    penalty_modified_by BIGINT,
    penalty_modified_at TIMESTAMP,
    cancelled_at TIMESTAMP,
    closed_at TIMESTAMP
);

CREATE TABLE attachments (
    id BIGSERIAL PRIMARY KEY,
    sdm_id BIGINT NOT NULL REFERENCES sdm(id),
    stored_filename TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    content_type TEXT,
    size_bytes BIGINT NOT NULL,
    uploaded_by BIGINT NOT NULL REFERENCES users(id),
    uploaded_at TIMESTAMP NOT NULL,
    deleted_at TIMESTAMP
);

CREATE TABLE workflow_events (
    id BIGSERIAL PRIMARY KEY,
    sdm_id BIGINT NOT NULL REFERENCES sdm(id),
    event_type TEXT NOT NULL,
    from_step_index INTEGER,
    to_step_index INTEGER,
    from_role TEXT,
    to_role TEXT,
    actor_id BIGINT NOT NULL REFERENCES users(id),
    assigned_to_id BIGINT REFERENCES users(id),
    note TEXT,
    created_at TIMESTAMP NOT NULL
);

CREATE TABLE password_reset_tokens (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP NOT NULL
);

CREATE INDEX idx_sdm_current_work ON sdm (current_assignee_id, status, created_at);
CREATE INDEX idx_sdm_creator_status ON sdm (created_by, status, created_at);
CREATE INDEX idx_sdm_reports_date ON sdm (memo_date, status);
CREATE INDEX idx_sdm_reports_master ON sdm (line_id, station_id, contractor_id, reason_id);
CREATE INDEX idx_attachments_sdm ON attachments (sdm_id, deleted_at);
CREATE INDEX idx_workflow_events_sdm ON workflow_events (sdm_id, id);
CREATE INDEX idx_reset_tokens_user ON password_reset_tokens (user_id);

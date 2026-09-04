# Service Deficiency Memo (SDM) Digitization App

Working Flask implementation for the SDM workflow. The local build uses SQLite for easy development. For office deployment, the production target is PostgreSQL — the database layer is backend-agnostic and switches on the `SDM_DATABASE_URL` environment variable.

## What is implemented

- Login with user roles and admin access.
- Change password (logged-in users) and forgot-password reset links (from the login page).
- Master data for lines, stations, contractors, users, and workflow defaults.
- SDM drafts and submission.
- Automatic organization-wide SDM numbering on first submission, allocated atomically so concurrent submissions cannot receive the same number.
- Unique Foil No. enforced at the database level.
- Dynamic reason-specific fields.
- Sequential workflow: Station Controller → Station Manager → Line Manager → Dy. HOD → Concerned Cell.
- Return only to the immediately previous officer.
- Station Controller edit until supervisor acceptance.
- Returned SDM editing/resubmission.
- Cancellation by Line Manager, Dy. HOD, or HOD while assigned to them.
- Multiple attachments with a 5 MB per-file limit.
- Attachment deletion only while the SDM is still a draft.
- Alternate next officer selection for leave/availability, constrained to the next workflow role.
- Audit history and printable SDM view.
- Concerned Cell search/report dashboard with CSV export.

## Run locally

```powershell
python -m flask --app run init-db
python run.py
```

Open `http://127.0.0.1:5000`.

## Seed users

| Role | Username | Password |
| --- | --- | --- |
| Admin | admin | 123 |
| HOD | hod | 123 |
| Concerned Cell | cell | 123 |
| Employee accounts | employment number | 123 |

## Database backends

The app reads one environment variable to pick the backend:

- **SQLite (default, development):** uses `SDM_SQLITE_DATABASE` (default `instance/sdm.sqlite`).
- **PostgreSQL (production):** set `SDM_DATABASE_URL` to a `postgresql://...` URL and install `psycopg[binary]` (already in `requirements-production.txt`). Run `init-db` once to create and seed the schema from `deployment/postgresql_schema.sql`.

Queries are written in a portable SQL subset (`IS TRUE`/`IS FALSE` booleans, `RETURNING` for inserted ids); only the placeholder style (`?` vs `%s`) differs and is converted internally by `app/db.py`.

If you already have an SQLite database created before the forgot-password feature existed, bring it up to date once with:

```powershell
python migrate_db.py
```

## Tests

```powershell
pip install -r requirements.txt
python -m pytest
```

The suite covers workflow permission rules, atomic SDM numbering (including daily rollover), forward/alternate assignee selection, the auth flows (login, change password, reset password), and an end-to-end SDM create → forward walkthrough.

## Ambiguities kept explicit

- Allowed attachment file types were not finalized. This build allows common office/image files: PDF, PNG/JPG, Word, and Excel.
- Alternate officer handling is constrained to the next workflow role. It does not allow skipping stages.
- Workflow order is database-backed through `workflow_stages`, but full drag/drop hierarchy editing is intentionally not built yet.
- No mail server is configured, so the password reset link is displayed on the confirmation page instead of being emailed. Token links expire after 30 minutes and are single-use.

## Production recommendation

Use Flask + PostgreSQL for the real deployment. `20,000` SDMs/year is manageable, but the workflow has multiple users, audit history, attachments, and reporting. PostgreSQL is the safer database for concurrent office use.

See `docs/production-architecture.md` and `deployment/postgresql_schema.sql`.

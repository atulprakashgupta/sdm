# SDM Production Architecture Decision

## Recommendation

Use this stack for the real office deployment:

- Web application: Flask
- UI: Bootstrap
- Production database: PostgreSQL
- Local development database: SQLite
- Windows-friendly production server: Waitress behind IIS/Apache/Nginx, or a Linux server with Gunicorn/Nginx
- Attachments: filesystem storage with database metadata
- Backups: daily PostgreSQL backup plus daily attachment-folder backup

## Why PostgreSQL

`20,000` SDMs per year is not a large record count. The reason to choose PostgreSQL is not raw volume; it is workflow reliability.

This system needs:

- multiple officers working at the same time
- strict audit history
- unique SDM numbering
- unique Foil No.
- reporting across years
- reliable backup/restore
- safe concurrent updates

SQLite is acceptable for the first working build and local testing. PostgreSQL is the better production database once several users are entering, forwarding, returning, and reporting SDMs at the same time.

## Database Size Estimate

At `20,000` SDMs/year:

- `5` years: about `100,000` SDMs
- `10` years: about `200,000` SDMs

That is small for PostgreSQL. The attachment files will consume far more storage than the database rows.

## Deployment Shape

```text
Users
  ↓
Browser
  ↓
Web Server / Reverse Proxy
  ↓
Flask App
  ↓
PostgreSQL Database
  ↓
Attachment Folder
```

## Production Rules

- Do not use Flask's development server for production.
- Use a long random `SECRET_KEY`.
- Store uploads outside the application source folder.
- Back up both the database and uploaded attachments.
- Keep SDM numbers and Foil numbers enforced by database constraints.
- Keep audit history append-only.

## Current Project Status

The app runs on SQLite by default and the database layer is now backend-agnostic: setting `SDM_DATABASE_URL` switches the app to PostgreSQL (schema in `deployment/postgresql_schema.sql`, applied by `init-db`), with portable SQL and atomic SDM numbering. The remaining work before office deployment is production hardening: enforcing a real `SECRET_KEY`, forcing first-login password changes, and configuring the attachment backup.

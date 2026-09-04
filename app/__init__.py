import os
from pathlib import Path

from flask import Flask

from . import db


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)

    base_dir = Path(__file__).resolve().parent.parent
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-change-this-secret"),
        DATABASE=os.environ.get("SDM_SQLITE_DATABASE", str(base_dir / "instance" / "sdm.sqlite")),
        # Set SDM_DATABASE_URL (postgresql://...) to use PostgreSQL instead of SQLite.
        DATABASE_URL=os.environ.get("SDM_DATABASE_URL"),
        UPLOAD_FOLDER=os.environ.get("SDM_UPLOAD_FOLDER", str(base_dir / "instance" / "uploads")),
        MAX_FILE_SIZE=5 * 1024 * 1024,
        MAX_CONTENT_LENGTH=50 * 1024 * 1024,
        ALLOWED_ATTACHMENT_EXTENSIONS={
            "pdf",
            "png",
            "jpg",
            "jpeg",
            "doc",
            "docx",
            "xls",
            "xlsx",
        },
    )

    if test_config:
        app.config.update(test_config)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)

    db.init_app(app)

    from . import admin, auth, reports, routes

    app.register_blueprint(auth.bp)
    app.register_blueprint(routes.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(reports.bp)
    app.add_url_rule("/", endpoint="index")

    return app

import json
import os
import uuid
from pathlib import Path

from flask import current_app
from werkzeug.utils import secure_filename

from . import db


def parse_reason_payload(form):
    return {
        "complaint_no": form.get("complaint_no", "").strip(),
        "overcharging_status": form.get("overcharging_status", "").strip(),
        "inspection": {
            "location_no": form.get("location_no", "").strip(),
            "eos_receipt_no": form.get("eos_receipt_no", "").strip(),
            "date_of_check": form.get("date_of_check", "").strip(),
            "time_of_check": form.get("time_of_check", "").strip(),
            "net_amount_eos": form.get("net_amount_eos", "").strip(),
            "amount_found": form.get("amount_found", "").strip(),
            "hidden_cash_found": form.get("hidden_cash_found", "").strip(),
            "result": form.get("result", "").strip(),
            "accountal": form.get("accountal", "").strip(),
        },
    }


def reason_payload_from_row(sdm):
    try:
        return json.loads(sdm["reason_payload"] or "{}")
    except json.JSONDecodeError:
        return {}


def allowed_file(filename):
    if "." not in filename:
        return False
    extension = filename.rsplit(".", 1)[1].lower()
    return extension in current_app.config["ALLOWED_ATTACHMENT_EXTENSIONS"]


def save_attachments(sdm_id, files, user_id):
    saved = []
    upload_root = Path(current_app.config["UPLOAD_FOLDER"]) / str(sdm_id)
    upload_root.mkdir(parents=True, exist_ok=True)

    for file in files:
        if not file or not file.filename:
            continue

        original_name = secure_filename(file.filename)
        if not allowed_file(original_name):
            raise ValueError(f"{file.filename} is not an allowed file type.")

        file.seek(0, os.SEEK_END)
        size = file.tell()
        file.seek(0)
        if size > current_app.config["MAX_FILE_SIZE"]:
            raise ValueError(f"{file.filename} exceeds the 5 MB per-file limit.")

        extension = original_name.rsplit(".", 1)[1].lower()
        stored_name = f"{uuid.uuid4().hex}.{extension}"
        file.save(upload_root / stored_name)
        db.get_db().execute(
            """
            INSERT INTO attachments
            (sdm_id, stored_filename, original_filename, content_type, size_bytes, uploaded_by, uploaded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (sdm_id, stored_name, original_name, file.content_type, size, user_id, db.now_iso()),
        )
        saved.append(original_name)
    return saved


def attachment_url_path(attachment):
    return f"uploads/{attachment['sdm_id']}/{attachment['stored_filename']}"

"""
Entry point for production deployments (Railway, Heroku, etc).
Gunicorn will import this as: gunicorn app:app
"""
import os
from pathlib import Path
from app import create_app, db

app = create_app()

# Initialize database on startup if it doesn't exist
with app.app_context():
    db.init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

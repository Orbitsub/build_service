"""
WSGI entry point — used by Gunicorn in production.

    gunicorn --workers 2 --threads 2 --bind 127.0.0.1:8000 wsgi:app
"""
from dotenv import load_dotenv

# Load .env before importing the app so environment variables are available.
load_dotenv()

from build_service_app import app  # noqa: E402 (import after load_dotenv is intentional)

if __name__ == "__main__":
    app.run()

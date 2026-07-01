import os

# Timeout for large file uploads
timeout = int(os.getenv("GUNICORN_TIMEOUT", "120"))
workers = int(os.getenv("GUNICORN_WORKERS", "2"))

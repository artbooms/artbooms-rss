"""Automatically read by the existing Render command: gunicorn app:app."""
import os

bind = "0.0.0.0:" + os.environ.get("PORT", "10000")
workers = max(1, int(os.environ.get("WEB_CONCURRENCY", "1")))
threads = 4
timeout = 120
graceful_timeout = 30
preload_app = False
accesslog = "-"
errorlog = "-"
access_log_format = '%(t)s pid=%(p)s method=%(m)s path="%(U)s" status=%(s)s duration=%(L)s user_agent="%(a)s"'


def post_worker_init(worker):
    from app import start_worker
    start_worker()


def worker_exit(server, worker):
    from app import stop_worker
    stop_worker()

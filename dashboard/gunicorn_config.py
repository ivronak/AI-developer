"""Gunicorn settings for the server. ONE worker on purpose: Claude runs and deploy jobs are threads inside this process and
are tracked in memory — a second worker would not see them. Threads give enough parallel requests for a small team."""
import os

bind = os.environ.get("GUNICORN_BIND", "127.0.0.1:5090")
workers = 1
worker_class = "gthread"
threads = 8
timeout = 120
graceful_timeout = 30
accesslog = "-"
errorlog = "-"

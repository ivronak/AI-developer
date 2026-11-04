"""Production entry point (the server): gunicorn -c gunicorn_config.py wsgi:app — behind OpenLiteSpeed on the same machine.
run.py stays the local development server. Defaults to production; refuses to start without SECRET_KEY / ENCRYPTION_KEY."""
import os

os.environ.setdefault("FLASK_ENV", "production")

from werkzeug.middleware.proxy_fix import ProxyFix  # noqa: E402

from app import create_app  # noqa: E402

app = create_app()
# OpenLiteSpeed is the only proxy in front: trust exactly one hop for the visitor's IP and https
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

#!/bin/bash
# Rehearsal before switching a REAL live site to release folders (CHANGES Step 6): makes a locked copy of the project
# as a new dashboard project "<name> rehearsal" whose "live" is that copy — then the owner tries "Switch live to
# release folders" (and Go Live / Rollback) on the copy first. The real live site is only READ (code + database copy).
# Usage (from dashboard/):  bash deploy/rehearsal.sh <project id>      e.g. 3 = Iv Vision Crm
#   After "Switch live to release folders" on the rehearsal project:
#                           bash deploy/rehearsal.sh golive <rehearsal project id>   (Go Live with the current code, then rollback)
set -euo pipefail
cd "$(dirname "$0")/.."
HOST=root@200.97.162.57
SITE=aided.ivinfotech.com
APP=/home/$SITE/app
KEY="$HOME/.ssh/aidash_deploy_ed25519"
SSH=(ssh -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$HOME/.ssh/aidash_known_hosts" "$HOST")
if [ "${1:-}" = "golive" ]; then
  RID=${2:?"usage: bash deploy/rehearsal.sh golive <rehearsal project id>"}
  [[ "$RID" =~ ^[0-9]+$ ]] || { echo "project id must be a number"; exit 1; }
  "${SSH[@]}" "SITEUSER=\$(stat -c %U /home/$SITE); runuser -u \$SITEUSER -- bash -c 'umask 007; set -a; . /etc/ai-dashboard/dashboard.env; set +a; cd $APP; FLASK_APP=wsgi.py venv/bin/flask rehearse-go-live $RID' </dev/null"
  exit 0
fi
SRC=${1:?"usage: bash deploy/rehearsal.sh <project id>"}
[[ "$SRC" =~ ^[0-9]+$ ]] || { echo "project id must be a number"; exit 1; }

"${SSH[@]}" bash -s -- "$SITE" "$APP" "$SRC" <<'REMOTE'
set -euo pipefail
SITE=$1; APP=$2; SRC=$3
SITEUSER=$(stat -c %U "/home/$SITE")
as_site() { runuser -u "$SITEUSER" -- bash -c "umask 007; set -a; . /etc/ai-dashboard/dashboard.env; set +a; cd $APP; $1"; }
mkdir -p "$APP/instance/backups"
cp -p "$APP/instance/app.db" "$APP/instance/backups/app-before-rehearsal-$(date +%Y%m%d-%H%M%S).db"

echo "→ rehearsal project (copy of project #$SRC)"
ID=$(as_site "venv/bin/python -W ignore - $SRC" <<'PY' | tail -1
import sys
from app import create_app
from app.extensions import db
from app.models import Environment, Project
app = create_app()
with app.app_context():
    src = db.session.get(Project, int(sys.argv[1]))
    if src is None or src.env("live") is None or not src.env("live").deploy_path:
        raise SystemExit("That project has no live server folder.")
    slug = (src.slug + "-rehearsal")[:60]
    p = Project.query.filter_by(slug=slug).first()
    if p is None:
        skip = {"id", "name", "slug", "cc_session_id", "beta_recipe", "live_recipe", "created_at", "updated_at"}
        p = Project(**{c.name: getattr(src, c.name) for c in Project.__table__.columns if c.name not in skip})
        p.name, p.slug, p.monitor_enabled = src.name + " rehearsal", slug, False
        db.session.add(p)
        db.session.flush()
        live = src.env("live")
        keep = {"kind", "url", "branch", "server_host", "server_id", "ssh_user", "deploy_path", "db_name"}
        db.session.add(Environment(project_id=p.id, **{k: getattr(live, k) for k in keep}))
        db.session.commit()
    print(p.id)
PY
)
HAVE=$(as_site "venv/bin/python -W ignore - $ID" <<'PY' | tail -1
import sys
from app import create_app
from app.extensions import db
from app.models import Project
app = create_app()
with app.app_context():
    p = db.session.get(Project, int(sys.argv[1]))
    b = p.env("beta")
    print("yes" if b is not None and b.managed and b.deploy_path and p.beta_recipe.strip() else "no")
PY
)
if [ "$HAVE" = "yes" ]; then
  echo "→ project #$ID already has its copy site — not building it again"
else
  echo "→ project #$ID — Claude now builds its locked copy site (10–20 minutes; the real live site is only read)"
  as_site "FLASK_APP=wsgi.py venv/bin/flask beta-setup $ID" </dev/null || echo "  (the setup job reported a problem — checking the copy site anyway)"
fi

echo "→ the copy becomes this rehearsal project's \"live\""
as_site "venv/bin/python -W ignore - $ID" <<'PY'
import sys
from app import create_app
from app.extensions import db
from app.models import Project
app = create_app()
with app.app_context():
    p = db.session.get(Project, int(sys.argv[1]))
    live, beta = p.env("live"), p.env("beta")
    if beta is None or not beta.managed or not beta.deploy_path:
        live.server_id, live.deploy_path = None, None      # never leave the rehearsal pointing at the REAL live site
        db.session.commit()
        raise SystemExit("The copy site was not made — see the job on the project page. "
                         "(The rehearsal project no longer points at the real live site.)")
    for k in ("url", "server_id", "deploy_path", "db_name", "site_user", "db_user", "db_password_enc", "beta_password_enc"):
        setattr(live, k, getattr(beta, k))
    live.layout, live.shared, live.restart_units, live.current_release, live.git_origin = "", [], [], None, None
    beta.server_id, beta.deploy_path, beta.managed = None, None, False   # nothing deploys a beta onto the copy
    db.session.commit()
    print(f"✓ ready: {p.name} — live (copy) = {live.url}")
    print("  Next: open this project in the dashboard → Settings → “Switch live to release folders…”")
PY
REMOTE

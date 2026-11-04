#!/bin/bash
# Deploy the dashboard code from this Mac to the server: code only — instance/ (database + keys) is never sent.
# Backs up the database, installs packages, runs migrations, restarts ONLY ai-dashboard, then health-checks.
# Usage (from dashboard/):  bash deploy/deploy.sh --git      (owner, 2026-11-04: push to GitHub first, the server pulls that commit)
#                           bash deploy/deploy.sh            (old way: upload this folder)   first time:  bash deploy/deploy.sh --setup
# --git also saves the code guard's checkpoint from the pulled source, so there is nothing to do by hand afterwards.
set -euo pipefail
cd "$(dirname "$0")/.."
HOST=root@200.97.162.57
SITE=aided.ivinfotech.com
APP=/home/$SITE/app
KEY="$HOME/.ssh/aidash_deploy_ed25519"
SSH=(ssh -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$HOME/.ssh/aidash_known_hosts" "$HOST")

REPO_URL=https://github.com/ivronak/AI-developer.git      # public repo: the server pulls over HTTPS with no key
REPO=/var/lib/aidash-repo
STAGE=/tmp/aidash-upload-$$
MODE=${1:-}
if [ "$MODE" = "--git" ]; then
  [ -z "$(git -C .. status --porcelain -- .)" ] || { echo "✗ dashboard/ has changes that are not committed — commit and push first"; exit 1; }
  SHA=$(git -C .. rev-parse HEAD)
  git -C .. fetch -q origin main
  [ "$(git -C .. rev-parse origin/main)" = "$SHA" ] || { echo "✗ GitHub's main is not this commit ($SHA) — push first"; exit 1; }
  echo "→ the server pulls $SHA from GitHub"
  "${SSH[@]}" bash -s -- "$REPO_URL" "$REPO" "$SHA" "$STAGE" <<'PULL'
set -euo pipefail
URL=$1; REPO=$2; SHA=$3; STAGE=$4
touch /run/aidash-deploying                 # the code guard does not alarm while a deploy is half done
[ -d "$REPO/.git" ] || git clone -q "$URL" "$REPO"
git -C "$REPO" fetch -q origin main
git -C "$REPO" checkout -q --detach "$SHA"
rm -rf "$STAGE" && mkdir -p "$STAGE"
rsync -a --exclude=/venv --exclude=/instance --exclude=/tests --exclude=__pycache__ --exclude=.DS_Store --exclude='._*' "$REPO/dashboard/" "$STAGE/"
PULL
  MODE=""
else
  echo "→ uploading code"
  COPYFILE_DISABLE=1 tar --no-xattrs --no-mac-metadata --exclude=./venv --exclude=./instance --exclude=./tests --exclude='__pycache__' --exclude='.DS_Store' --exclude='._*' -czf - . \
    | "${SSH[@]}" "rm -rf $STAGE && mkdir -p $STAGE && tar -xzf - -C $STAGE"
fi

"${SSH[@]}" bash -s -- "$STAGE" "$SITE" "$APP" "$MODE" <<'REMOTE'
set -euo pipefail
STAGE=$1; SITE=$2; APP=$3; MODE=${4:-}
if [ "$MODE" = "--setup" ]; then bash "$STAGE/deploy/setup_server.sh" "$SITE"; fi
SITEUSER=$(stat -c %U "/home/$SITE")
TS=$(date +%Y%m%d-%H%M%S)
if [ -f "$APP/instance/app.db" ]; then
  mkdir -p "$APP/instance/backups"
  cp -p "$APP/instance/app.db" "$APP/instance/backups/app-before-deploy-$TS.db"
  echo "→ database backed up: instance/backups/app-before-deploy-$TS.db"
fi
echo "→ installing code"
rsync -a --delete --exclude=/venv --exclude=/instance "$STAGE/" "$APP/"
rm -rf "$STAGE"
chown -R "$SITEUSER:$SITEUSER" "$APP"
mkdir -p "$APP/instance" && chown "$SITEUSER:$SITEUSER" "$APP/instance" && chmod 700 "$APP/instance"
as_site() { runuser -u "$SITEUSER" -- bash -c "umask 007; set -a; . /etc/ai-dashboard/dashboard.env; set +a; cd $APP; $1"; }
[ -x "$APP/venv/bin/python" ] || as_site "python3.9 -m venv venv"
echo "→ packages"
as_site "venv/bin/pip install -q --upgrade pip >/dev/null; venv/bin/pip install -q -r requirements.txt"
echo "→ migrations"
as_site "FLASK_APP=wsgi.py venv/bin/flask db upgrade"
echo "→ restarting ai-dashboard (only this app)"
systemctl restart ai-dashboard
for i in 1 2 3 4 5 6 7 8 9 10; do
  sleep 1
  code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:5090/login || true)
  [ "$code" = "200" ] && { echo "✓ healthy (login page 200)"; exit 0; }
done
echo "✗ health check failed — last 30 log lines:"; journalctl -u ai-dashboard -n 30 --no-pager
exit 1
REMOTE

if [ "${1:-}" = "--git" ]; then
  echo "→ code guard checkpoint (from the pulled source, tests included)"
  MSG=$(git -C .. log -1 --format=%s)
  "${SSH[@]}" aidash-code-guard save "$REPO/dashboard" "$(printf %q "git ${SHA:0:7}: $MSG")"
fi

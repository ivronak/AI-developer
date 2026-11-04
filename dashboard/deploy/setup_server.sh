#!/bin/bash
# One-time server setup for the AI Project Dashboard (run as root ON the server; safe to run again — never overwrites secrets).
# Needs the CyberPanel website to exist first (it creates the site's Linux user and /home/<site>).
# Touches only: its own user / group / folders / env file / sudoers file / systemd units. Never other websites.
set -euo pipefail
SITE="${1:-aided.ivinfotech.com}"
HOME_DIR="/home/$SITE"
APP="$HOME_DIR/app"
RUNNER=aided-runner                 # low-rights user that runs the projects' test commands
WSGROUP=aided-ws                    # shared group: dashboard user + runner, only for Claude's checkouts
WSROOT=/srv/ai-dashboard/workspaces
ENVF=/etc/ai-dashboard/dashboard.env

[ -d "$HOME_DIR" ] || { echo "No $HOME_DIR — create the website in CyberPanel first."; exit 1; }
SITEUSER=$(stat -c %U "$HOME_DIR")
echo "site user: $SITEUSER"

id "$RUNNER" >/dev/null 2>&1 || useradd --system --create-home --home-dir "/var/lib/$RUNNER" --shell /sbin/nologin "$RUNNER"
getent group "$WSGROUP" >/dev/null || groupadd --system "$WSGROUP"
usermod -aG "$WSGROUP" "$SITEUSER"
usermod -aG "$WSGROUP" "$RUNNER"
chmod 700 "/var/lib/$RUNNER"

mkdir -p /srv/ai-dashboard "$WSROOT"
chmod 755 /srv/ai-dashboard
chown "$SITEUSER:$WSGROUP" "$WSROOT"
chmod 2770 "$WSROOT"                # setgid: everything inside stays in the shared group

mkdir -p "$APP"
chown "$SITEUSER:$SITEUSER" "$APP"
chmod 750 "$APP"

mkdir -p /etc/ai-dashboard
if [ ! -f "$ENVF" ]; then
  SK=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
  EK=$(python3 -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())')
  cat > "$ENVF" <<ENV
FLASK_ENV=production
SECRET_KEY=$SK
ENCRYPTION_KEY=$EK
GUNICORN_BIND=127.0.0.1:5090
PUBLIC_URL=https://$SITE
WORKSPACE_ROOT=$WSROOT
TEST_RUNNER_USER=$RUNNER
ENV
  # a root-only copy of the encryption key: losing it makes every saved token / key unreadable
  umask 077; echo "$EK" > /root/ai-dashboard-encryption-key.backup
  echo "created $ENVF (new secrets)"
else
  echo "kept existing $ENVF"
fi
chown "root:$SITEUSER" "$ENVF"
chmod 640 "$ENVF"

SUDO=/etc/sudoers.d/ai-dashboard
cat > "$SUDO.tmp" <<SUD
# AI Project Dashboard: its user may run commands only as the low-rights test runner — nothing else, never root.
Defaults:$SITEUSER !requiretty
$SITEUSER ALL=($RUNNER) NOPASSWD: ALL
# Claude Code engine: only its login may pass through to the runner — no other variable
Defaults:$SITEUSER env_keep += "CLAUDE_CODE_OAUTH_TOKEN ANTHROPIC_API_KEY MCP_TOOL_TIMEOUT MCP_TIMEOUT"
SUD
visudo -cf "$SUDO.tmp" >/dev/null && mv "$SUDO.tmp" "$SUDO" && chmod 440 "$SUDO"

# Claude Code CLI for the runner (engine "claude_code"), git that accepts the dashboard's checkouts
if [ ! -x "/var/lib/$RUNNER/.local/bin/claude" ]; then
  sudo -u "$RUNNER" -H bash -c 'cd ~ && curl -fsSL https://claude.ai/install.sh | bash' >/dev/null
fi
sudo -u "$RUNNER" -H git config --global --get-all safe.directory 2>/dev/null | grep -qx '*' || sudo -u "$RUNNER" -H git config --global --add safe.directory '*'
# the runner must not reach this server's Redis (other websites use it)
if command -v firewall-cmd >/dev/null && ! firewall-cmd --permanent --direct --query-rule ipv4 filter OUTPUT 0 -p tcp --dport 6379 -m owner --uid-owner "$RUNNER" -j REJECT >/dev/null 2>&1; then
  firewall-cmd --permanent --direct --add-rule ipv4 filter OUTPUT 0 -p tcp --dport 6379 -m owner --uid-owner "$RUNNER" -j REJECT >/dev/null
  firewall-cmd --direct --add-rule ipv4 filter OUTPUT 0 -p tcp --dport 6379 -m owner --uid-owner "$RUNNER" -j REJECT >/dev/null
fi

# Node LTS for beta builds (React / Vite / Next…) in its own folder — existing sites keep their Node (owner OK 2026-10-17).
NODE_LINE=latest-v24.x
if [ ! -x /opt/aidash-node/bin/node ]; then
  T=$(mktemp -d)
  curl -fsSL "https://nodejs.org/dist/$NODE_LINE/SHASUMS256.txt" -o "$T/SUMS"
  F=$(grep -oE 'node-v[0-9.]+-linux-x64\.tar\.xz' "$T/SUMS" | head -1)
  curl -fsSL "https://nodejs.org/dist/$NODE_LINE/$F" -o "$T/$F"
  (cd "$T" && grep " $F\$" SUMS | sha256sum -c - >/dev/null) || { echo "Node download failed its checksum — not installed"; rm -rf "$T"; exit 1; }
  D=/opt/${F%.tar.xz}
  mkdir -p "$D" && tar -xJf "$T/$F" -C "$D" --strip-components=1
  ln -sfn "$D" /opt/aidash-node
  rm -rf "$T"
  echo "installed $(/opt/aidash-node/bin/node -v) in /opt/aidash-node"
fi

HERE=$(cd "$(dirname "$0")" && pwd)
for u in ai-dashboard.service ai-dashboard-tick.service ai-dashboard-tick.timer; do
  sed -e "s#__SITEUSER__#$SITEUSER#g" -e "s#__APP__#$APP#g" "$HERE/$u" > "/etc/systemd/system/$u"
done
systemctl daemon-reload
systemctl enable ai-dashboard.service ai-dashboard-tick.timer >/dev/null
echo "setup done"

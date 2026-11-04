#!/bin/bash
# One-off cleanup the owner approved on 2026-10-21 ("group 1"): the two rehearsal copy sites, the old hand-made
# crm.beta site, their service / database, and their backups. Nothing else is touched — every name is exact.
# Usage (from dashboard/):  bash deploy/cleanup-2026-10-21.sh
set -euo pipefail
cd "$(dirname "$0")/.."
HOST=root@200.97.162.57
KEY="$HOME/.ssh/aidash_deploy_ed25519"
SSH=(ssh -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$HOME/.ssh/aidash_known_hosts" "$HOST")

cat <<'TXT'
This PERMANENTLY removes (cannot be undone):
  - website iv-vision-crm-rehearsal.beta.ivinfotech.com + its service (copy of iv-crm for the rehearsal)
  - website eye-rehearsal.beta.ivinfotech.com + database eye_rehearsal (copy of Eye HMS for the rehearsal)
  - website crm.beta.ivinfotech.com + service crm-beta-backend (old hand-made iv-crm beta, already stopped)
  - their backups in /root/ai-dashboard-backups, the eye-hms-rehearsal snapshots, and the duplicate
    iv-crm backup from the first (stopped) switch attempt
NOT touched: crm.ivinfotech.ca, eyenosis.com, iv-vision-crm.beta, eye-saas-hms.beta, the dashboard, other sites.
TXT
read -r -p "Type YES to delete: " answer
[ "$answer" = "YES" ] || { echo "Nothing deleted."; exit 0; }

"${SSH[@]}" 'bash -s' <<'REMOTE'
set -uo pipefail
SKIP="Example|iv-vision-crm-rehearsal.beta.ivinfotech.com|eye-rehearsal.beta.ivinfotech.com|crm.beta.ivinfotech.com"
check() { for d in /usr/local/lsws/conf/vhosts/*/; do s=$(basename "$d"); echo "$s" | grep -qxE "$SKIP" && continue
          echo "$s $(curl -s -o /dev/null -m 15 -w '%{http_code}' "https://$s/" || echo 000)"; done | sort; }
echo "Disk before: $(df -h /home | tail -1 | awk '{print $4" free"}')"
check > /tmp/sb.$$
for u in aidash-iv-vision-crm-rehearsal-beta-crm-backend.service crm-beta-backend.service; do
  systemctl disable --now "$u" 2>/dev/null; rm -f "/etc/systemd/system/$u"; echo "service removed: $u"
done
systemctl daemon-reload
mysql -e "DROP DATABASE IF EXISTS eye_rehearsal; DROP USER IF EXISTS 'r_eye_rehearsal'@'localhost';" && echo "database removed: eye_rehearsal"
for h in iv-vision-crm-rehearsal.beta.ivinfotech.com eye-rehearsal.beta.ivinfotech.com crm.beta.ivinfotech.com; do
  cyberpanel deleteWebsite --domainName "$h" >/dev/null 2>&1 || true
  if [ -d "/home/$h" ]; then echo "website NOT fully removed: $h (folder still there)"; else echo "website removed: $h"; fi
done
( cd /root/ai-dashboard-backups && rm -f iv-vision-crm-rehearsal.beta.ivinfotech.com-* eye-rehearsal.beta.ivinfotech.com-* \
    crm.ivinfotech.ca-iv-crm-before-releases-20261021-104847.tgz ) && echo "rehearsal backups removed"
( cd /root/ai-dashboard-snapshots && rm -f eye-hms-rehearsal-*.sql.gz ) && echo "rehearsal snapshots removed"
sleep 5
check > /tmp/sa.$$
if diff -q /tmp/sb.$$ /tmp/sa.$$ >/dev/null; then echo "All other sites answer exactly as before."; else echo "CHANGED SITES:"; diff /tmp/sb.$$ /tmp/sa.$$; fi
rm -f /tmp/sb.$$ /tmp/sa.$$
echo "Still running: $(systemctl is-active iv-crm-backend) iv-crm · $(systemctl is-active aidash-iv-vision-crm-beta-crm-backend) iv-crm beta · $(systemctl is-active ai-dashboard) dashboard"
echo "Disk after: $(df -h /home | tail -1 | awk '{print $4" free"}')"
REMOTE

# Deploying the AI Project Dashboard

## The server (set up 2026-10-15)
The dashboard runs on the owner's **IV VPS** (`200.97.162.57`, AlmaLinux 9, CyberPanel + OpenLiteSpeed, Python 3.9).
The same machine runs **many live websites, including the Verixa CRM with real users** — nothing here may touch them.

| What | Where |
|---|---|
| URL | `https://aided.ivinfotech.com` (DNS A → the VPS; Let's Encrypt via CyberPanel, auto-renew) |
| Website (CyberPanel) | `aided.ivinfotech.com`, site user `aided5498`, made with `cyberpanel createWebsite` |
| App code + venv | `/home/aided.ivinfotech.com/app` (venv `app/venv`), database `app/instance/app.db` (instance/ = mode 700) |
| Secrets | `/etc/ai-dashboard/dashboard.env` (root:aided5498, 640): `SECRET_KEY`, `ENCRYPTION_KEY` (never change it — every saved token becomes unreadable; root-only copy in `/root/ai-dashboard-encryption-key.backup`), `GUNICORN_BIND=127.0.0.1:5090`, `PUBLIC_URL`, `WORKSPACE_ROOT`, `TEST_RUNNER_USER` |
| App service | `ai-dashboard.service` — Gunicorn, 1 worker × 8 threads (gthread) on `127.0.0.1:5090`, `UMask=0007` |
| Every 5 minutes | `ai-dashboard-tick.timer` → `flask monitor` (sites, reminders, AI budget alerts) + `flask github-sync` |
| Claude's checkouts | `/srv/ai-dashboard/workspaces` (aided5498:aided-ws, 2770) |
| Test runner | Linux user `aided-runner` (no login, group `aided-ws`). `/etc/sudoers.d/ai-dashboard` lets the dashboard user run commands **only as that user** — it can't read the dashboard's DB / secrets or other sites (checked) |
| Claude Code engine | CLI installed for `aided-runner` at `/var/lib/aided-runner/.local/bin/claude` (official installer; re-run `setup_server.sh` to install on a new server); sudoers keeps only `CLAUDE_CODE_OAUTH_TOKEN` / `ANTHROPIC_API_KEY`; firewalld direct rule: runner can't reach Redis `:6379` |
| Web server | vhost `/usr/local/lsws/conf/vhosts/aided.ivinfotech.com/vhost.conf`: `extprocessor aidedash` (proxy → 127.0.0.1:5090) + `context /`, http→https rewrite (ACME path excluded). `wsgi.py` uses ProxyFix (1 hop) |
| Config backups taken before editing | `/root/httpd_config.conf.before-aided-*`, `/root/aided-vhost.conf.before-proxy-*` |
| SSH from the Mac | `~/.ssh/aidash_deploy_ed25519` (root), host key pinned in `~/.ssh/aidash_known_hosts` |

## Deploy a code change (from the Mac, in `dashboard/`)
```
bash deploy/deploy.sh
```
Uploads code only (never `instance/`, `venv/`, `tests/`), backs up the database to `instance/backups/`, installs packages,
runs migrations, restarts **only** `ai-dashboard`, health-checks `127.0.0.1:5090/login`. Run the flow tests first.
First install on a new server: `bash deploy/deploy.sh --setup` (runs `setup_server.sh`, which never overwrites secrets).

## Claude model list for the chat (added 2026-10-23, root job — NOT part of `deploy.sh`)
The chat's model + effort pickers show the models **Claude Code itself lists** (its `/model` picker), never a hard-coded list and
never from the Anthropic API. The dashboard's own Claude login is an inference-only token, for which Claude Code shows only 5
aliases; the VPS's full Claude login shows all of them. So a small root job reads the list and the dashboard only reads a file:

| What | Where |
|---|---|
| Job | `/usr/local/sbin/aidash-claude-models` (root:root 750; copy in `deploy/aidash-claude-models`) |
| Units | `ai-dashboard-models.service` + `ai-dashboard-models.timer` (hourly, 3 min after boot) in `/etc/systemd/system/` |
| Output | `/etc/ai-dashboard/claude-models.json` (root:aided5498 640) — model name, description, effort levels, plus the VPS Claude's plan, a masked e-mail hint (`j***@gmail.com`), connector names and status and whether `claude-web.service` runs; never the full e-mail, organisation or tokens |
| Reader | `app/cc_models.py` (used by the chat, Settings → Claude Code and the agent run) |

It makes no model call (no usage spent; it also runs `claude mcp list` as root, which does not refresh the login) and skips a run when the VPS
login is within 20 minutes of expiring, so it never refreshes the login that the long-running `claude remote-control` uses.
**`deploy.sh` does not install the root job:** after changing `deploy/aidash-claude-models` or the units, copy them to `/usr/local/sbin/` and
`/etc/systemd/system/` by hand and run `systemctl daemon-reload`. On any failure the previous file stays. If the file is
missing the chat falls back to Quick / Normal / Best; Settings shows a warning when the list is more than 3 hours old.
Install on a new server: copy the three files to the paths above, `systemctl daemon-reload`, `systemctl enable --now ai-dashboard-models.timer`.
`deploy.sh` never touches these, but the database migration `c3a91d5e7f20` (changes.agent_model / agent_effort) **is** part of the code.

## Code guard (added 2026-10-23, root timer — NOT part of `deploy.sh`)
The dashboard's source exists on the server and (older) on the Mac it was first deployed from — **not on GitHub**. `deploy.sh` makes the
server's code *identical* to the Mac's folder (`rsync --delete`), so a deploy from a copy that lacks our server-side work would silently erase it.
The guard keeps a git copy of every known-good state and tells a person when the running code stops matching it.

| What | Where |
|---|---|
| Tool | `/usr/local/sbin/aidash-code-guard` (root:root 750; copy in `deploy/aidash-code-guard`) |
| Units | `ai-dashboard-guard.service` + `.timer` (every 5 min, 2 min after boot) in `/etc/systemd/system/` |
| Git copy | `/var/lib/aidash-source` (`tree/` = the code, `state.json` = last checkpoint; root only) |
| Status for Settings | `/etc/ai-dashboard/code-guard.json` (root:aided5498 640) |
| Alerts | the dashboard's bell (`alerts` table, once per problem); Telegram too once it is set up in Settings (**not set up yet**) |

- **After EVERY deploy** (from any computer): `aidash-code-guard save /path/to/the/complete/source "what changed"` — SOURCE must equal what
  is deployed (tests/ aside), otherwise it refuses. To avoid an alert while a deploy is half done: `touch /run/aidash-deploying` first
  (`save` removes it; it also expires after 30 minutes).
- The guard alerts when the deployed files differ from the checkpoint, when the database's migration head is not one the code knows
  (a deploy from an older copy), or when the login page stops answering. It never changes the app by itself.
- `save` also compares with the last checkpoint and **refuses** when files or database migrations that checkpoint had are gone (that is what a deploy from
  an older copy looks like); `--force` overrides it on purpose. One alert per *kind* of problem (code differs / database / login page / git copy), so a
  changing count cannot start an alert storm.
- Put the old code back: `aidash-code-guard restore --dry-run` (shows what would change), then `aidash-code-guard restore [REV]`
  (backups of the code in `/root` and of the database in `instance/backups`, the list of packages in `/root`, files copied **as the site user** with
  checksums and verified, packages installed, migrations run, only `ai-dashboard` restarts; rolls back by itself if anything fails or the terminal is
  closed; refuses if the database is ahead of the code). Restoring an older revision makes it the newest checkpoint. A rollback restores code only —
  the messages name the database copy and package list to use if those need undoing too.
- **Before the first deploy from the Mac ever again:** copy the server's current code into the Mac's `dashboard/` folder (or deploy from the
  source kept by `save`), otherwise that deploy erases the picker, notes, cards, Debug mode and root commands.

## Root commands (added 2026-10-23, root service — NOT part of `deploy.sh`)
Claude (every Claude Code change run) can ask for ONE command as root: the owner sees the exact command in a chat card, types a 6-digit code from an
authenticator app, and exactly that command runs. The dashboard never holds root; a small root service does.

| What | Where |
|---|---|
| Service | `/usr/local/sbin/aidash-rootd` (root:root 750; copy in `deploy/aidash-rootd`) + `aidash-rootd.service` in `/etc/systemd/system/` |
| Socket | `/run/aidash-rootd/rootd.sock` (root:aided5498, 0660, and the uid is checked again with SO_PEERCRED: root and `aided5498` only) |
| Secret | `/etc/aidash-rootd/totp.json` (root 0600) — created by `aidash-rootd enroll`; **not** in the dashboard database |
| Log | `/var/log/aidash-rootd.log` (every authorize / refuse / exec, with the command; never the code or the ticket) |

- One-time set-up, by the owner in a server terminal: `aidash-rootd enroll` (shows a setup key for Google Authenticator / Authy, asks for the first code).
  `aidash-rootd reset` removes it (root commands are then off). Switch the whole thing off: `systemctl disable --now aidash-rootd`.
- A code works once, approves one command (its SHA-256 is bound to a ticket valid 2 minutes, and to the authenticator that existed then:
  `reset` voids open approvals).
- Wrong codes lock it: 5 within 10 minutes **or** 10 within 24 hours → locked 15 min; each further lock is longer (1 h, 6 h, 24 h; back to 15 min after
  a quiet week). The counters survive a restart. The Settings page shows the lock and the dashboard raises an alert when it locks. If YOU are
  locked out: `aidash-rootd unlock` (root, on the server). `aidash-rootd status` shows the state.
- A command's output goes to a root-only file, not a pipe: restarting the service does not kill a running command or a service it started.
  At the time limit the command gets SIGTERM, SIGKILL only 15 s later; output over 64 MB is cut. A command is **not** run if its start cannot be logged.
- Install on a new server: copy the script to `/usr/local/sbin/` (750), the unit to `/etc/systemd/system/` and `deploy/aidash-rootd.logrotate` to
  `/etc/logrotate.d/aidash-rootd`, `systemctl daemon-reload`, `systemctl enable --now aidash-rootd`, then `aidash-rootd enroll`.
  The migration `f8a2c4e6b1d3` (changes.agent_debug, change_questions.ticket) **is** part of the code. Claude Code must run as a separate Linux user
  (`TEST_RUNNER_USER`); otherwise the dashboard refuses root commands (its shell could reach the socket).
- Honest limit: the card is drawn by the dashboard, so someone who fully controls the dashboard process *and* tricks the owner into
  typing a code for a misleading card could still get a command through. It stops a prompt-injected Claude, a developer account and anyone without the owner's phone.

## Git in Claude's checkouts (hardened 2026-10-23)
Claude's shell (user `aided-runner`, group `aided-ws`) can write in `/srv/ai-dashboard/workspaces/<project>/`, and the dashboard (user `aided5498`,
with the GitHub token on the command line) runs git there afterwards. Found by review: a prompt-injected Claude could plant a hook / fsmonitor /
filter command or a push URL in `.git`. Now (`app/workspace.py`): before every git call the dashboard keeps only the config keys git itself writes,
empties `.git/hooks`, removes `.git/info/attributes`, refuses a `.git` that is not its own, and runs git with `core.hooksPath=/dev/null`,
`core.fsmonitor=false` and `umask 027`; `prepare()` takes group/other write away from everything under `.git` (the runner can still read it).
`bash harden_workspaces.sh audit|lock` (in the work folder, not shipped) checks and locks all existing checkouts at once. Claude can no longer
`git add/commit` inside a checkout (the rules already forbid it); the dashboard commits and pushes.

## Rules
- Restarting `ai-dashboard` affects only this app. Restarting OpenLiteSpeed affects **every** website — only after a vhost /
  listener change, and only with the owner's OK.
- First owner account: `flask create-owner --name … --email …` (as the site user, env loaded) prints a one-time setup link.

## Claude accounts (2026-11-04)
Claude Code logs in only with **web-login Claude accounts** (Settings → Claude Code → Claude accounts). Nothing to install:
each account's folder is `/var/lib/aided-runner/claude-accounts/a<id>` (made by the dashboard as `aided-runner`, mode 700,
`projects/` → `/var/lib/aided-runner/.claude/projects`). "Main account" = the runner's own `~/.claude` login made by hand
before. Sudoers is unchanged (the folder is passed as an `env` argument). The 5-minute tick (`flask monitor`) checks each
account hourly and renews a run-out login. Removing an account runs `claude auth logout` and deletes only its own folder;
revoke it on claude.ai too. The old setup-token was blanked by migration `a1c3e5f7b9d2` — revoke it on claude.ai
(Settings → Claude Code tokens) if it is still listed there.

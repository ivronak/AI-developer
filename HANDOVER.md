# HANDOVER — AI Project Management Dashboard (state on 2026-10-22)

Paste this to a new Claude session. Read it first, then `CLAUDE.md` → `MEMORY.md` (newest at the bottom, full detail) →
`TRACKER.md` → `CHANGES.md` (the roadmap) in this folder.

## 1. Who / how to work
- Owner: non-technical, writes Hindi / Hinglish by voice. Reply in **simple Hindi (Devanagari)**, technical words in
  English, **at most ~5 short lines** per answer. Docs and code in English; the dashboard UI is English only.
- After every change: add an entry to `MEMORY.md`, update `TRACKER.md` (+ `CHANGES.md` step status, `CLAUDE.md` for new
  rules). The owner should never have to ask.
- Ask the owner only for real decisions. Anything irreversible / on live servers: owner's explicit OK first.
- No git commit / push unless the owner asks (repo `ivronak/AI-developer`, branch `main`).

## 2. What it is
A Flask + Jinja dashboard (`dashboard/`) where the owner adds client projects from GitHub, writes changes in a chat,
**Claude makes them on a branch → automatic beta → client approval → Go Live (OTP) → Rollback**. Goal ("zero manual
work", `CHANGES.md`, 11 steps): betas for any stack made by Claude, safe Go Live with release folders, mobile apps
(Flutter) test builds, etc. Overall ≈ 70 % done.

## 3. Where things run
- **Live dashboard:** https://aided.ivinfotech.com — IV VPS `200.97.162.57` (AlmaLinux 9, CyberPanel + OpenLiteSpeed
  1.9.1). The same server runs the Verixa CRM (real users) and ~20 client sites — **never touch them**.
- Code on the server: `/home/aided.ivinfotech.com/app` (user `aided5498`), env `/etc/ai-dashboard/dashboard.env`
  (never change `ENCRYPTION_KEY`), service `ai-dashboard` (gunicorn 127.0.0.1:5090), timer every 5 min (monitor).
- SSH from the Mac: `ssh -i ~/.ssh/aidash_deploy_ed25519 -o IdentitiesOnly=yes -o UserKnownHostsFile=~/.ssh/aidash_known_hosts root@200.97.162.57`
- **Deploy:** `cd "AI Project/dashboard" && bash deploy/deploy.sh` — Claude may run it itself (permission rule in
  `/Users/rj/Desktop/claude project/.claude/settings.local.json`). Always run the tests first:
  `source venv/bin/activate && PYTHONPATH=. python -W ignore tests/flows.py` (424 checks green on 2026-10-22).
- Claude Code on the server runs as `aided-runner` (group `aided-ws`), login = owner's `claude setup-token` token in
  Settings → Claude Code. Never as root.
- Workspaces (git checkouts per project): `/srv/ai-dashboard/workspaces/<slug>` (must be group-writable — done by
  `workspace.share_with_runner`).

## 4. Projects in the live dashboard
| # | Project | Live | Beta | Notes |
|---|---|---|---|---|
| 1 | AI Developer | the dashboard itself | — | never Go Live it |
| 2 | Test Site | — | test-site.beta.ivinfotech.com | |
| 3 | Iv Vision Crm (React + Flask + SQLite) | crm.ivinfotech.ca — **release folders** | iv-vision-crm.beta.ivinfotech.com | live steps written by Claude; service iv-crm-backend runs as root |
| 4 | Eye Saas Hms (Laravel + 2 Flutter apps) | eyenosis.com — **release folders** | eye-saas-hms.beta.ivinfotech.com | apps `eye_care_app`, `eye_care_tab`; change #7 = forgot password (OTP) on web/app/tab, on beta |
| 5, 6 | rehearsal projects | sites deleted | — | owner should delete these 2 projects in the dashboard |

Betas: wildcard DNS `*.beta.ivinfotech.com`; each beta is password-locked (user `beta`, password on the project card).
For betas with an API for apps/SPAs the lock covers pages only (`/api/` open, app's own login) and the vhost runs
**HTTP/1.1** (`enableSpdy 0`) because OLS 1.9 resets HTTP/2 POSTs in that regex context.

## 5. Built (main pieces, all deployed)
- Automatic beta by Claude with bounded MCP tools (`app/betas.py`, `app/beta_tools.py`), beta never talks to live
  (frontend address replaced, publish refuses live URLs), no live secrets on beta.
- Go Live v2 with release folders (`app/releases.py`): backup, copy-on-write release, shared data, instant switch,
  automatic switch-back, rollback in seconds, SQLite snapshot/restore, Claude-written live steps.
- Mobile apps (`app/mobile.py`, `app/codemagic.py`): Android test APKs on Codemagic pointed at the beta, built **only
  when the owner presses Build** (project page highlights a change that touched app code), short QR link `/d/<id>/<token>`.
- Claude in change runs: web search, beta debug tools (beta_request / beta_logs / beta_run / beta_probe /
  beta_access_log / beta_webserver), `ask_owner` question/permission cards (waits ≤30 min), permission prompt for any
  tool outside its list, the account's claude.ai connectors may load (check with Settings → "Test Claude Code").
- Project page redesign (compact header, Beta/Live cards, Apps tab, folded chat, live steps, "N steps" on replies).

## 6. Open / next
- **Step 11 daily backups** (next recommended): owner must choose where — cloud (Cloudflare R2 / Backblaze B2) or this server.
- Eyenosis: rotate the secrets that were exposed via `public/env` (file removed 2026-10-17); live has `APP_DEBUG=true`
  / `APP_ENV=local`; Telescope registered unconditionally (developer fix). The forgot-password change shows the OTP on
  screen — must send it by email before Go Live.
- iOS (TestFlight): owner must pick the Apple account; needs App Store Connect API key (Issuer ID, Key ID, .p8) in Settings.
- Steps 7 (beta storage/freshness), 8 (smoke tests), 9 (95 % measurement), 11 (move other projects) not started.
- Server cleanup later (with owner OK): iv-crm + eyenosis pre-switch backups in `/root/ai-dashboard-backups` and the
  old `*.before-releases-*` folders.
- Limited deploy user instead of root SSH for the dashboard (security item); a full security review.

## 7. Hard rules / lessons (details in CLAUDE.md §4)
- The permission system **blocks**: Claude (server or local) editing web-server configs that affect all sites, root
  tools for the server Claude, permanent deletions, and writes to the live dashboard DB from ad-hoc scripts → give the
  owner a script / ask instead (e.g. `deploy/cleanup-2026-10-21.sh`, `deploy/rehearsal.sh`).
- One checkout = one job at a time (an app build and a beta deploy on the same checkout broke a deploy).
- Tests never call real APIs / SSH / Claude / Codemagic (fakes in `tests/flows.py`).
- Release folders: never `a && b` for must-succeed steps in remote scripts; live SQLite only moved while its service is stopped.
- Before the owner's live sites change: backup, check every site before/after, never restart OpenLiteSpeed except
  graceful restarts for beta work (standing OK), and say exactly what changed.

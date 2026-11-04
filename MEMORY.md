# MEMORY — what is built, what changed, where things are (newest at the bottom)

Owner rule (2026-10-17): after **every change or milestone**, add an entry here so the next session remembers
exactly what was done. Keep entries short: date · what · where (files / server) · how it was checked · what's next.
Big picture lives in `TRACKER.md` (status, decisions) and `CHANGES.md` (the "zero manual work" roadmap).

## Snapshot (2026-10-17)
- **Live dashboard:** https://aided.ivinfotech.com on the IV VPS (200.97.162.57, AlmaLinux 9, CyberPanel +
  OpenLiteSpeed). Same machine runs the Verixa CRM (real users) and ~15 client sites — never touch them.
  Deploy only with `bash deploy/deploy.sh` from `dashboard/` (tests first). Map: `dashboard/deploy/DEPLOY.md`.
- **SSH from the Mac:** `~/.ssh/aidash_deploy_ed25519` (root), host key in `~/.ssh/aidash_known_hosts`.
- **Built (v1 steps):** sign-in + authenticator, users & log, projects, GitHub sync, monitor + Telegram, Claude chat
  (engine Claude API or Claude Code on the server, one session per project), beta deploy + auto beta deploy after a
  passed change, client approval link, Go Live / Rollback (code ready, real run pending), AI cost page, automatic
  beta for any project (first real one: Eye HMS).
- **Claude Code on the server:** runs as low-rights users only (`aided-runner` for chat changes, the beta website's
  own user for beta setup). Login since 2026-11-04 = **Claude accounts with a web login** (Settings → Claude Code →
  Claude accounts, any number, picked per chat message); the setup-token is retired. Never root (blocked by policy).
- **Source of truth for the dashboard code = the server** (`/var/lib/aidash-source/tree`, the code guard's git copy).
  Work done on the server on 2026-10-23 (model picker, notes, root commands, code guard) was copied into the Mac's
  `dashboard/` on 2026-11-04. Before any future deploy from the Mac, check the Mac copy still matches the server's.
- **Projects on the live dashboard:** 1 AI Developer (points at the dashboard itself — don't Go Live it),
  2 Test Site (beta test-site.beta.ivinfotech.com), 3 Iv Vision Crm (hand-made beta crm.beta.ivinfotech.com),
  4 Eye Saas Hms (automatic beta eye-saas-hms.beta.ivinfotech.com, live = eyenosis.com).
- **Beta domains:** wildcard DNS `*.beta.ivinfotech.com` → VPS (Hostinger DNS). Standing OK for a graceful
  OpenLiteSpeed restart when a beta website is made / locked (check all other sites after).

## Log
- **2026-10-17 · Automatic beta (CHANGES pre-step).** `app/betas.py` (`run_setup`, `run_managed_deploy`),
  `remote.push_dir`, Claude Code `run_as` + `workdir`, migration `0a3304ff3c73` (environments.managed / site_user /
  db_user / db_password_enc, projects.beta_recipe), Settings → Automatic beta panel, `flask beta-setup <id>`.
  Eye HMS beta made with no hand work after start: website + SSL, 2.6 GB DB copy → `eye_saas_hms_beta`, code,
  beta `.env` (staging, debug off, mail → log, Razorpay / mail / AWS blank), Claude (as `eyesa9835`) ran composer,
  migrate, storage:link, caches; recipe saved. Checked from outside; live untouched. 291 flow checks.
  Claude's notes: system Node 16 (Vite needs 20+), app loads Telescope (dev package).
- **2026-10-17 · Roadmap.** `CHANGES.md` written (11 steps). Owner answered all 7 questions (recorded there);
  question 5 (release folders on live) waits for a symlink test on Test Site's beta.
- **2026-10-17 · Site survey.** 20 sites: Laravel ×6, React+Flask ×1, Python/Node apps ×3, static ×3; left out of
  dashboard deploys: Verixa CRM, ai.ivinfotech.com (third-party tool), the dashboard, host default site.
- **2026-10-17 · Step 1 test (beta password).** OpenLiteSpeed ignores `.htaccess` auth, so the lock is a vhost
  `realm` + `context /` block (marker `AIDASH-BETA-LOCK`) with `/home/<beta>/.beta-htpasswd` (MD5-crypt,
  lsadm:nobody 640). Tried by hand on test-site.beta and eye-saas-hms.beta: 401 without, 200 with the password,
  Laravel pages + CSS work, `/.env` 404, ACME path stays open, all other sites fine. Vhost backups in
  `/root/<beta>-vhost.conf.before-password-*`. (These two hand-set passwords are not stored — the code version
  will set new ones.) Next: put it in the dashboard code (every new beta locked, password shown in the dashboard
  and on the client approval page).
- **2026-10-17 · Step 1 done — every beta behind a password.** `betas.lock()` / `run_lock()` (vhost realm +
  `context /`, marker `AIDASH-BETA-LOCK`, `/home/<beta>/.beta-htpasswd`, password piped to `openssl passwd -1 -stdin`
  so it never shows in a process list), `environments.beta_password_enc` (migration `52875f7ba0cd`), graceful
  restart only when a vhost gets its first lock, with every site checked before/after (a site that stops answering →
  job fails + alert). New betas are locked right after the website exists, before code/data arrive. Smoke checks use
  the password. Project page shows "Locked · user beta · password …" (Copy); owner buttons "Lock the beta with a
  password" / "New beta password"; client approval page shows the beta login. CLI `flask beta-lock <id>
  [--new-password]`. 297 flow checks. Deployed. Locked all three betas from the dashboard: eye-saas-hms.beta,
  test-site.beta, crm.beta (iv-crm, its /api proxy still works) — each 401 without / 200 with the password; other
  sites 200/302 after the restart.
  Next: Step 2 (Claude's bounded server tools).
- **2026-10-17 · Step 2 built — Claude makes the beta with bounded tools.** `app/beta_tools.py` (status,
  make_website, copy_database mysql + sqlite (read-only online backup), put_code, write_env (any settings file,
  secrets blanked, never live's DB), run (as the website user, no sudo / systemctl / chown / ssh), read_file /
  write_file (inside the beta folder, never .env), start_service (systemd unit as the website user on a free port
  6100–6999, `$PORT`), service_logs, check_site, save_recipe (`@restart NAME` lines), report_problem (alert +
  proposed fix — Claude never edits a tool)). `app/blueprints/internal_mcp.py` = MCP over HTTP at
  `127.0.0.1:5090/internal/mcp` (local only — X-Forwarded-For → 404; one-job Bearer key stored as
  `jobs.tool_token_hash`, migration `74f677c2108b`; key dies when the job ends; CSRF-exempt on purpose).
  `betas.run_setup` = Claude Code as `aided-runner` with ONLY `mcp__beta` tools (`--strict-mcp-config`, built-in
  tools disallowed), prompts SETUP_PROMPT / FIX_PROMPT, rules TOOL_RULES; `run_managed_deploy` = put_code + replay
  recipe without Claude, Claude (tools) only when a step fails. `deploy.env_script(..., filename=)`. Config
  `INTERNAL_URL`. sudoers env_keep += MCP_TOOL_TIMEOUT MCP_TIMEOUT. 308 flow checks (fake Claude calls the real
  tools; refusals tested). Deployed (`deploy.sh --setup`). Checked: local endpoint without key 401, public 404.
  Real test started: Eye HMS beta rebuilt by Claude with the tools (`flask beta-setup 4`, log
  `instance/beta-setup-4b.log`).
- **2026-10-17 · Step 2 verified for real.** Claude Code (subscription login, as `aided-runner`, tools only) rebuilt
  the Eye HMS beta on its own: status → make_website → copy_database (105 tables) → put_code (main had moved on to
  d3cdfa1a — picked up automatically) → write_env → composer / migrate / caches → check_site → save_recipe. No tool
  problem reported. Checked: `/` and `/login` 200 with the password, 401 without, `.env` and `.git` 404, live's
  `.env` untouched, eyenosis.com 200. Recipe now uses lsphp84 for artisan. Next: Step 3 (beta starts by itself when
  project details are saved) and Step 4 (Node 20+, iv-crm React + Flask + SQLite).
- **2026-10-17 · Step 3 done — beta starts by itself.** `betas.maybe_auto_setup(project, user)`: after Project
  details or Servers & deploy are saved, if live server + folder are known and the project has no beta on a server
  yet (and no job runs), a `beta_setup` job starts (Claude + tools) and the page jumps to Deploys. The beta address
  in Project details is now optional (empty → `<slug>.beta.ivinfotech.com`). Projects with a hand-made beta
  (Test Site, iv-crm) are not touched. 311 flow checks. Deployed.
- **2026-10-17 · Step 4 (part) — any stack.** Node 24 LTS installed separately at `/opt/aidash-node` (official
  tarball, SHA256 checked, `setup_server.sh`); system Node 16 untouched. Tools: `put_code place=webroot|app` (app =
  code in `/home/<beta>/app`, outside the web root), `publish` (built folder → web root), `write_file web:…`,
  recipe `@publish FOLDER`, `web_errors` (beta's web-server error lines), `start_service` now registers the port as
  an OpenLiteSpeed external app `127.0.0.1:<port>` **in that beta's vhost only** (`betas.allow_proxy`, marker
  `AIDASH-PROXY-<port>`, backup + graceful restart with all sites checked — `betas.restart_web`), tool output hides
  secret values (`_redact`). Operator CLI `flask beta-tool <id> <tool> '<json>'`. 319 flow checks. Deployed.
  **iv-crm automatic beta by Claude:** https://iv-vision-crm.beta.ivinfotech.com — code in app/, SQLite crm.db
  copied read-only, Flask service `aidash-iv-vision-crm-beta-crm-backend` (user ivvis3933, port 6100), React built
  with Node 24 and published, `.htaccess` (/api proxy + SPA fallback). First run: /api gave 500 — Claude reported it
  with report_problem (OpenLiteSpeed needs the port as an external app); fixed in the tool, service restarted with
  the operator CLI → `/api/health` 200, `/api/auth/me` 401 (not logged in), pages 200, `.env`/`.git` 403,
  `crm.db` not downloadable, live untouched (iv-crm-backend active, crm.ivinfotech.ca 200).
  Open: the old hand-made beta `crm.beta.ivinfotech.com` (+ service `crm-beta-backend` on 5051) is no longer the
  project's beta — remove it? (owner). Next: Step 5 (git problems solved by itself).
- **2026-10-17 · Step 5 done — git problems handled by itself.** `projects.github_repo_id` (GitHub's id; sync matches
  by id first, so a renamed / transferred repo updates `github_repo` + logs "GitHub repo moved" instead of a new or
  "missing" project), `environments.git_origin` / `git_checked_at` (migration `9451229f11e5`), `app/gitcheck.py`
  (`repo_slug`, `check_origin`, `problem`, daily `check_due` from the monitor tick; an origin that differs is resolved
  through GitHub (`github.resolve_repo`, cached in setting `repo_aliases`) — an old name of the same repo is fine).
  Go Live's drift check now also reads the origin and refuses a truly different repo; the project page warns.
  Settings → GitHub explains why other accounts' repos don't appear. Old iv-crm beta service `crm-beta-backend`
  stopped + disabled (files kept, owner to confirm deletion). 324 flow checks. Deployed. Real check: iv-crm and
  Eye HMS live folders still use `ivalema35/...` URLs, which GitHub resolves to the same repo ids as
  `ivronak/...` (transfer redirect) → no warning. Next: Step 6 (Go Live v2) — first the symlink test on a beta.
- **2026-10-17 · Step 6 test — release switch by symlink.** On test-site.beta (restored afterwards): web root as a
  symlink to `releases/rN`; `ln -s … next && mv -T next public_html` switches instantly for static files, no
  web-server restart, the beta password stays. **PHP keeps running the old release until that site's lsphp workers
  restart** (`pkill -u <site user> lsphp` → new release at once; only that site). A removed file may be served from
  cache for a moment right after a switch. Owner's answer 5 = **A: ask before converting each live site.**
  Plan: release engine → rehearsal on a fake-live copy under *.beta → Claude fixes inside the new release →
  app-type (iv-crm) → real sites one by one with the owner's OK.
- **2026-10-17 · Step 6 part 1 — Go Live v2 engine + rehearsal.** `app/releases.py`: `plan_convert` / `run_convert`
  (one-time per site, owner + authenticator, route `projects.live_convert`, panel "Safe Go Live (release folders)"):
  tar backup → `cp -a --reflink=auto` (XFS CoW, checked: `reflink=1`) to `releases/r…` → `.env*`, `storage`, SQLite
  files, untracked upload dirs moved to `shared/` and linked → catch-up rsync → `mv` folder aside + symlink → that
  site's lsphp workers / services restart → health check (with the beta password on locked sites) → undo if it
  doesn't answer. `run_go_live_v2`: drift check (files newer than `.release-made`), DB snapshot, squash release +
  tests + push, new release = CoW copy of current + `rsync --checksum --delete` of the new code (shared and deps
  excluded), live recipe (`projects.live_recipe` or the beta recipe) as the site user in the NEW release, then switch
  (`ln -s … .next && mv -T`), restart, health → switch straight back + alert if it fails; keep last 3. 
  `run_rollback_v2`: switch to the previous release (+ optional snapshot). Go Live / Rollback routes pick v2 when
  `environments.layout == "releases"` (migration `94fe8fbaaedf`: layout, shared, restart_units, current_release,
  projects.live_recipe). Refuses (for now) sites whose web root is outside the code folder (app-type, iv-crm).
  336 flow checks (also fixed a time-flaky replay test). Deployed.
  **Rehearsal on the real server** (fake live = copy of the Eye HMS beta at eye-rehearsal.beta.ivinfotech.com,
  docRoot public_html/public like eyenosis, own DB `eye_rehearsal`, locked; dashboard project #5 "Eye HMS
  rehearsal"): convert → 427/427 checks OK during it; Go Live v2 (composer, migrate, caches in the new release) →
  251/251 OK; Rollback v2 → back in 3.1 s, 5/6 checks OK — one in-flight request dropped while the site's PHP
  workers restarted. Disk: release copies share blocks (CoW); `du` shows full sizes. Fixed during rehearsal: plan
  script exit status; rsync now by checksum so unchanged files keep sharing disk.
  Next in Step 6: Claude fixes a failing release step inside the new release; app-type layout (iv-crm); then real
  sites one by one with the owner's OK.
- **2026-10-17 · Step 6 part 2 — Claude fixes a failing step inside the NEW live release.** `jobs.context`
  (migration `77d8974b37bc`) carries `{"mode": "release_fix", release, run_user, recipe, error}`; in that mode the
  MCP tool set shrinks to status / run / read_file / web_errors / save_recipe (→ `projects.live_recipe`) /
  report_problem (`beta_tools.RELEASE_TOOLS`); `run` refuses anything that could touch the live database, shared
  files or git (`LIVE_DANGER`: migrate:fresh/refresh/reset/rollback, db:wipe/seed, tinker, mysql/psql/sqlite3/…,
  DROP/TRUNCATE/DELETE FROM/UPDATE SET/ALTER TABLE, git push/commit/reset/checkout/clean, `../`) and any absolute
  path outside the release (except /usr, /bin, /opt/aidash-node, lsphp bins, /tmp). A Go Live job without that
  context gets no tools. `releases._claude_fixes_release`: Claude (RELEASE_RULES: no code edits, no DB) → every step
  runs again → switch only if all pass; else the new release is deleted and live stays. 343 flow checks. Deployed.
  **Real rehearsal** (project #5, steps changed to `composer install --no-dev`): the step failed (the app loads
  Telescope, a dev-only package, unconditionally); Claude found the cause and **refused to install dev tools on live
  ("could expose data")**, saved nothing, explained the code fix for beta → Go Live stopped, the failed release was
  removed, the rehearsal live kept answering (256/256 checks). Steps restored afterwards.
  Finding for the developer: Eye HMS registers Telescope unconditionally (`TelescopeServiceProvider`), and live
  eyenosis.com runs with APP_ENV=local / APP_DEBUG=true.

- **2026-10-17 · Step 6 part 3 — release folders for the app-type layout (iv-crm: React build + Flask + SQLite).**
  `app/releases.py`: runtime data is now found by comparing the live folder with the repo's tracked files
  (`tracked_files` from the dashboard's checkout → `SCAN_PY` on the server: untracked folders, `.env*`, SQLite files;
  deps/build folders never; loose untracked files listed as "not kept"), plus the systemd services that run from the
  folder (→ `restart_units` by itself). Web root outside the code folder → copied into `<release>/.webroot` and the
  web root becomes a link to `<code>/.webroot`, so ONE switch changes both. A live SQLite database is never copied:
  consistent backup first (sqlite backup as the file's owner), then it MOVES into shared/ while the site's services
  are stopped for those seconds (`trap` puts the old folders back and starts the services on any failure; a site with
  SQLite but no service of its own is refused). After the switch: health + every service must be active, else back.
  Go Live v2: `@publish FOLDER` → built files into the new release's `.webroot` (old hashed files kept), venv script
  paths rewritten to the new release (so `backend/venv/bin/pip` can't install into the serving release), switches
  are separate commands (no silent half-switch with `a && b`). Claude never fixes a release owned by root.
  Server test: SQLite moved under a running writer → 170/170 writes kept, integrity ok. Read-only scans: iv-crm →
  backend/.env, crm.db, logs, secrets, uploads/projects + `iv-crm-backend.service` ✔; eyenosis → .env, storage,
  public/storage, iv-backup-agent ✔. 354 flow checks (incl. `bash -n` of every generated script).
  NOT yet deployed (production deploy needs the owner's OK) — then rehearsal on a copy site
  `crm-rehearsal.beta.ivinfotech.com`, then the real sites one by one with the owner's OK.
  **Security finding (2026-10-17):** eyenosis.com serves `public/env` — a copy of the Laravel settings file
  (64 keys incl. APP_KEY) — publicly at https://eyenosis.com/env (HTTP 200). Reported to the owner; nothing changed.
  **Fixed with the owner's OK (2026-10-17 13:36):** `public/env` moved off the site to
  `/root/ai-dashboard-backups/eyenosis-public-env-removed-20261017-133651` (root-only 600); /env now 404, home +
  login 200. Still to do (owner): rotate the secrets that were in it.
- **2026-10-21 · Step 6 part 3 UI.** Project → Settings → "Safe Go Live" panel now lists the services restarted after a switch; the
  convert dialog explains the web-root move and the SQLite pause. Checked on a local DB copy (port 5071, copy deleted). 354 checks.
  Still only on the Mac — production deploy waits for the owner (the permission system blocks `deploy.sh` for me).
- **2026-10-21 · Step 6 part 3 deployed by the owner** (checked: new releases.py + panel on the server, dashboard active, login 200,
  no errors). My own writes to the live dashboard DB are blocked by the permission system, so: `dashboard/deploy/rehearsal.sh <id>`
  (owner runs it): backs up the dashboard DB, makes project "<name> rehearsal" (copy of project <id>, monitor off), runs
  `flask beta-setup` (Claude builds a locked copy site, real live only read), then makes that copy the rehearsal's "live"
  (beta row detached). Then the owner presses Settings → Switch live to release folders on it. For iv-crm: `bash deploy/rehearsal.sh 3`.
- **2026-10-21 · Rehearsal run 1 (owner ran `rehearsal.sh 3`).** Project #6 "Iv Vision Crm rehearsal" made; Claude built the locked
  copy site iv-vision-crm-rehearsal.beta (service `aidash-iv-vision-crm-rehearsal-beta-crm-backend` on 6101, /api/health 200,
  139 MB crm.db copy, recipe saved). Job failed only on a FALSE alarm: the smoke check called /.env "open" because a React
  site answers every address with index.html (200). Fix: `deploy.exposed(base, path, auth)` — a leak only when real
  settings / git content comes back (used by `betas._smoke` and the check_site tool); 5 new checks. Second bug: `claude -p`
  read the rest of the operator's script from stdin as part of its prompt, so the script's last step never ran (Claude
  correctly refused to run it) → `claude_code._spawn` now uses stdin=DEVNULL; rehearsal.sh passes </dev/null and skips
  re-building an existing copy; on failure it detaches the rehearsal from the real live site. Project #6's "live" still
  points at the REAL crm.ivinfotech.ca until the script is run again — the owner must not press Switch on it before that.
  359 checks. Not deployed yet (owner runs deploy.sh).
- **2026-10-21 · Rehearsal run 2 (owner deployed + ran rehearsal.sh 3 + pressed Switch on project #6).** Plan was right
  (web root public_html, shared backend/.env + backend/crm.db, service found by itself, backups incl. a consistent .sqlite).
  The switch happened, but the service did not come back → the AUTOMATIC UNDO put the old code folder + web root back (as
  designed). Cause: `shared/backend` was made by root, so the site's user couldn't create crm.db-wal/-shm next to the moved
  database ("attempt to write a readonly database"); the service kept failing after the undo too (the DB stays in shared/).
  Fixed by hand on the copy site (chown to the site user → API 200 again). Code fix: folders made inside shared/ copy the
  owner + rights of the same folder in the live code; after an undo the dashboard re-checks the site + services and alerts
  "check it NOW" if they still don't answer. 361 checks. Lesson: the undo restores folders, not the shared DB's folder rights
  — every shared/ folder must already be right BEFORE the switch. Next: owner deploys, presses Switch on #6 again.
- **2026-10-21 · Rehearsal run 3 — conversion OK on the iv-crm copy (job 33).** app → releases/r20261021-102110,
  public_html → app/.webroot, shared backend/.env + backend/crm.db (integrity ok, 36 tables), service running from the
  release, real iv-crm untouched (200, service active). Found: the site answered **403** — the web server (group nobody)
  could not traverse releases/ + the release (group = site user, 750); and `releases._health` counted 403 as up. Fixed
  by hand on the copy (chgrp nobody + g+x) → / /login /api/health + JS/CSS assets 200, no .env/.git leak. Code fixes:
  app layout gives releases/ + the release the web root's group with g+x; `_health` needs a status < 400. Added
  `releases.run_rehearse_go_live` + `flask rehearse-go-live <id>` (rehearsal projects only: Go Live v2 with the current
  main — nothing merged or pushed — then rollback) and `rehearsal.sh golive <id>`. 365 checks. Next: owner deploys and
  runs `bash deploy/rehearsal.sh golive 6`.
- **2026-10-21 · Rehearsal run 4 — Go Live + rollback on the iv-crm copy: OK (twice).** `rehearsal.sh golive 6`: new release
  from main fec642a (venv + pip, npm ci + vite build, @publish web/dist → .webroot, 161 assets), switch, site + service
  checked, rollback to r20261021-102110 in 4 s. venv scripts in the new release point at that release (proof: pip's own
  path) — the serving release was never installed into. Real crm.ivinfotech.ca 200 throughout. Prune now never removes
  the live release (after a rollback it is not the newest) and runs after a rehearsal. 366 checks. NOTE for the real
  iv-crm Go Live: its venv is backend/venv (service runs backend/venv/bin/gunicorn as root) but the beta recipe makes
  ./venv — set a live recipe for project 3 before its first Go Live; recipe steps there run as root (code owned by root),
  so Claude won't fix a failing step there.
- **2026-10-21 · Ready for the real iv-crm (owner said yes).** Go Live / rollback now handle SQLite: `golive.sqlite_path`,
  snapshot = consistent SQLite backup as the file's owner (.sqlite.gz), `golive.restore_script` swaps the file only while
  the site's services are stopped (also used by Rollback v2). Conversion fills `live.db_name = sqlite:<file>` + db_engine
  sqlite when empty. Settings: database type SQLite, DB name may be `sqlite:path/in/folder.db`. New owner-only field
  "Steps for a new live release" (`projects.save_live_steps`, @publish folder checked). 373 checks. Real iv-crm (read
  only): live = main fec642a, web/.env.production tracked (API = crm.ivinfotech.ca), venv = backend/venv. Live steps to
  paste for project 3: `backend/venv/bin/pip install -q -r backend/requirements.txt` / `cd web && npm ci --no-audit
  --no-fund && npm run build` / `@publish web/dist`. Not deployed yet.
- **2026-10-21 · Real iv-crm conversion attempt 1 (job 36): stopped safely at step 1.** The pre-conversion tar backup
  exited 1 ("crm.db: file changed as we read it" — the CRM writes all the time) → set -e stopped the script before
  anything moved. Checked: iv-crm + public_html untouched, service active, site 200, API 200. Left behind: the
  1.8 GB backup /root/ai-dashboard-backups/crm.ivinfotech.ca-iv-crm-before-releases-20261021-104847.tgz (complete apart
  from the busy DB; delete only with the owner's OK). Fix: the folder tar leaves SQLite files (+ -wal/-shm/-journal)
  out — they get their own consistent backup — and accepts tar's exit 1 (file changed), not 2; tried on the server.
  374 checks. Next: owner deploys and presses Switch on Iv Vision Crm again.
- **2026-10-21 · REAL crm.ivinfotech.ca now uses release folders (job 37, owner pressed Switch).** iv-crm → releases/
  r20261021-105400, public_html → iv-crm/.webroot, shared: backend/.env, crm.db, logs, secrets, uploads/projects (root-
  owned like before), service iv-crm-backend runs from the release, live db = sqlite:backend/crm.db (engine sqlite).
  Checked: / /login /api/health + assets 200, DB quick_check ok, no errors in the service log, last 400 API requests all
  200 (real users kept working), eyenosis + dashboard fine. /.env etc. answer 200 text/html = the React page (same as
  before; not the file). Old folder kept as iv-crm.before-releases-*; disk 66 → 70 GB used (two 1.8 GB folder backups +
  DB backups in /root/ai-dashboard-backups) — cleanup only with the owner's OK. Live steps not saved yet (owner pastes).
- **2026-10-21 · Claude writes the live steps itself (owner: "Claude khud dalega").** `releases.prepare_live_steps`: facts
  from the running release (venvs, build files, separate web root, service ExecStart/WorkingDirectory/User, shared items,
  beta steps) → Claude Code in a new MCP mode `live_steps` with READ-ONLY tools (status / read_file as the web server's
  user / save_recipe with the live filter / report_problem — no run) → `projects.live_recipe`. Runs by itself after a
  switch to release folders and before a site's first Go Live (if empty); owner button "Let Claude write the live
  steps" (job kind `live_steps`). The next Go Live tries the steps in a new release; a failing step never switches.
  internal MCP accepts live_convert / live_steps jobs. 377 checks. Not deployed yet.
- **2026-10-21 · CSS fix: oversized icons on the project page.** The beta card's "Locked · user beta…" line and the chat's
  "This project uses Claude Code on the server" hint had a bare inline `<svg>` with no size → it filled the whole width.
  Fix: `p > svg, .hint > svg, li > svg { width: 1em; height: 1em }` in base.css (dashboard + mock copy) — class-specific
  icon rules still win. Checked on the live page by injecting the rule in the browser only: no icon wider than 40 px on
  Chat / Changes / Deploys / Backups / Settings. Not deployed yet.
- **2026-10-21 · Deploy permission (owner: "niyam jod do").** Added `Bash(bash deploy/deploy.sh)` to
  `/Users/rj/Desktop/claude project/.claude/settings.local.json` (git-ignored there; the line was added to the parent
  .gitignore). Claude now deploys itself — tests first. Deployed the CSS fix: live page has no oversized icon, new CSS
  loaded. The owner already started a first CRM change ("Login page me welcome…").
- **2026-10-21 · Beta 401 = the password lock (owner's browser sent no password; no popup).** Beta itself fine (with
  password: / /login /api/health 200; change #4 "Login page me welcome to ivcrm" built in, beta 5e553c0). Added an
  "Open beta (signed in)" link on the beta card (https://beta:<pw>@host); checked in the browser pane: the login page
  opens and shows "Welcome to IVCRM". 378 checks, deployed by Claude.
- **2026-10-21 · BUG FIXED: the iv-crm beta's pages called the LIVE API.** Beta login said "Cannot reach the server":
  the managed beta deploy replayed `npm run build` without the beta address, so web/.env.production's
  VITE_API_URL=https://crm.ivinfotech.ca was built in. Live's CORS blocked it (only one OPTIONS from beta reached live;
  no data touched). Fix: `betas.front_env_script` runs before every beta build and writes .env.production.local with
  live's address → the beta's (Vite / CRA / Next read it first; put_code keeps it); `publish` refuses a build that
  still has live's address as a quoted server URL (prose like "Open https://live…" is allowed). Beta rebuilt +
  published: build uses https://iv-vision-crm.beta.ivinfotech.com, /api/health 200 from the page. 380 checks, deployed.
- **2026-10-21 · Beta asked for the password again and again after the CRM login (owner chose option A).** Cause: the
  CRM sends "Authorization: Bearer <token>" to /api/, which replaces the browser's beta (Basic) password → 401 →
  popup. OLS sub-contexts INHERIT the realm of `context /` (a plain /api/ context did not help). Working layout (made
  by hand on iv-vision-crm.beta, then in code `betas.open_api` / `OPEN_API_PY`, run after every `publish` when the
  beta's .htaccess proxies ^api/ to a 61xx service): realm removed from `context /`; `context /api/` = proxy to the
  service (AIDASH-API-OPEN-<port>); `context exp:^/(?!api/)(.*)$` with the realm + the SPA fallback written inline
  (OLS does not read .htaccess in a regex context) (AIDASH-BETA-LOCK-PAGES). Checked: without password pages 401,
  /api/health 200; with it / /login /dashboard + assets 200; API with a Bearer token 200; no .env/.git leak; every
  other site unchanged (checked before/after each graceful restart; vhost backups in /root/*before-lock-except-api*,
  *before-inline-spa*). 383 checks, deployed. Known limit: the "Open beta (signed in)" link leaves the password in
  the page's base URL, so an app that calls a RELATIVE /api/ url fails until the address is opened once without it
  (iv-crm uses an absolute URL — fine). Follow-up: land that link on the clean address.
- **2026-10-21 · Owner keeps answer A** (asked again: automatic switch on a first Go Live?) — each live site is switched to
  release folders only when the owner presses the button. Next real site: eyenosis.com (owner presses it when ready).
- **2026-10-21 · Server cleanup (owner OK'd group 1).** My own run was blocked (irreversible deletion), so
  `dashboard/deploy/cleanup-2026-10-21.sh` (owner runs it, types YES): removes iv-vision-crm-rehearsal.beta (+ service),
  eye-rehearsal.beta (+ DB eye_rehearsal + user r_eye_rehearsal), old crm.beta (+ crm-beta-backend), their backups,
  eye-hms-rehearsal snapshots and the duplicate iv-crm tar; checks every other site before/after. Careful: a grep for
  "crm.beta.ivinfotech.com" also matches the REAL iv-vision-crm.beta service — the script uses exact names only.
  Also seen: eyenosis.com switched to release folders by the owner (job 45, 12:32), site + login 200; Claude wrote its
  live steps (composer install without --no-dev). Dashboard projects #5/#6 (rehearsals) to be removed by the owner.
- **2026-10-21 · Cleanup done (owner ran cleanup-2026-10-21.sh, typed YES on the 2nd try).** Removed: the 2 rehearsal
  sites, old crm.beta, services, DB eye_rehearsal + user, rehearsal backups/snapshots, duplicate iv-crm tar. Free
  disk 29 → 38 GB. Checked: every other site answers as before (eye.ivinfotech.com was already down — not ours),
  iv-crm / iv-crm beta / dashboard running, betas locked (401). Kept: iv-crm + eyenosis pre-switch backups and old
  folders (remove in a few days with the owner's OK), eyenosis env evidence, iv-crm rollback snapshots.
- **2026-10-21 12:52 · Full live health check after the cleanup (read only): all fine.** 19/20 sites answer (eye.ivinfotech.com
  already down, not ours). iv-crm: login + API 200, service from release r20261021-120836, DB quick_check ok, 0 service
  errors in 3 h, last 500 API calls 499×200 + 1×201. eyenosis: login/register 200, release r20261021-123224, 0 Laravel
  errors since the switch, last 500 requests 494×200 + 6×404, DB 105 tables. Verixa CRM + dashboard fine (one 503 at
  11:16 = the dashboard restart during a deploy). Disk 38 GB free.
- **2026-10-22 · Step 10 part 1 — Android test apps on Codemagic (deployed).** `app/codemagic.py` (REST API, header
  x-auth-token, token encrypted in Settings → Codemagic, tests replace `codemagic.api`), `app/mobile.py` (find Flutter
  apps → `projects.mobile_apps`; `yaml_for` → codemagic.yaml pushed ONLY on its own branch `aidash/app-main` /
  `aidash/app-change-<id>`; before `flutter build apk` the live address in lib/ + assets/ is replaced by the beta's
  (perl, exact host) and the build stops if it is still there; `refresh` on page view + monitor timer → 7-day public link
  + QR on the project page + alert), model `AppBuild` (migration f33bf4017627), project panel "Mobile apps" (Look for
  apps / Test APK per app), auto build after a passed change that touched an app. 398 checks. Eyenosis repo: two Flutter
  apps eye_care_app + eye_care_tab (Android + iOS, app id com.example.*, API hard-coded http://eyenosis.com/api). Owner
  asked about cost / Expo: Expo can't build Flutter; Codemagic free = 500 min/month; GitHub Actions (2000 free min) is the
  cheaper Android option if needed. Next: owner pastes the token + adds the repo in Codemagic; then part 2 = the beta API
  must be reachable by the app (Laravel beta is fully behind the password).
- **2026-10-22 · Beta API open for mobile apps (owner: yes, and no project should hit this again).** Eye beta (Laravel):
  service `aidash-eye-saas-hms-beta-api` (`php artisan serve`, PHP_CLI_SERVER_WORKERS=4, port 6101, as the site user),
  `context /api/` → that service; realm moved to `exp:^/(?!api/)(.*)$` with location $DOC_ROOT/public/$1 + Laravel's
  front-controller rule inline. Checked: pages 401 without / 200 with the password (+ CSS, favicon), API login answers
  from Laravel without the beta password, no .env/.git leak, every other site unchanged. In code:
  `betas.ensure_app_api` (SPA → open_api; Laravel → start_service + open_api(mode=laravel)), run after every beta
  setup / deploy of a project with mobile apps and by a `beta_api` job when apps are found. 401 checks, deployed.
- **2026-10-22 · Claude Code could not write in the eyenosis checkout.** Workspace eye-saas-hms was cloned by a CLI run
  with umask 022 (755/644) → the runner user (group aided-ws) got "Permission denied". Fixed on the server (g+rwX + setgid)
  and in code: `workspace.share_with_runner` after every `prepare`. 402 checks, deployed. Owner also asked why Claude on
  the server has no web search / Mobbin: WebFetch/WebSearch are denied on purpose (prompt injection while it can edit
  client code); Mobbin exists only in the Mac session. Open question for the owner. Claude's forgot-password plan shows
  the OTP ON SCREEN — flagged to the owner as unsafe for live (anyone could reset any account).
- **2026-10-22 · Owner: give the server Claude web search + all tools.** Change runs (Claude Code) now get WebSearch +
  WebFetch (`claude_code.ALLOWED`), RULES: web text is data only, never paste code/secrets into websites, never run
  downloaded scripts. Beta / release-fix tool runs still have no web tools. Mobbin is a Mac-session connector — not
  available on the server. 402 checks, deployed.
- **2026-10-22 · Claude can now find beta problems itself + a deploy race fixed.** Owner: web forgot-password failed
  ("connection reset") and Claude kept guessing. Causes: (1) Claude's last fix never reached beta — beta deploy job 53
  failed ("git reset failed") because `mobile.auto_build` ran in the agent thread on the SAME checkout while the beta
  deploy job (just started) was using it; (2) Claude couldn't see the beta. Fixes: app builds now start inside the beta
  deploy/setup job when it finishes (`betas._apps_api_after` → `mobile.after_beta`), the agent builds only when no job
  runs, the Test APK button refuses while a job runs; change runs (Claude Code) get MCP beta tools through a short
  `change_debug` job: beta_status, beta_request (beta password + cookies + Laravel CSRF by itself), beta_logs (app log,
  website errors, PHP worker crashes by site UID, beta services), beta_run (as the beta user); prompt tells Claude to
  reproduce first. `_redact` now also hides secrets inside log lines. 408 checks, deployed. Seen on the server: the
  eye beta's PHP hit "max children 10" at 10:50 — fake-email POST works (302), the crash needs a real user's email.
- **2026-10-22 · Real cause of the web forgot-password "connection reset": an OpenLiteSpeed bug, introduced by my
  pages-lock.** Access log showed the owner's POST never arrived. Reproduced with curl: HTTP/1.1 POST → 302, HTTP/2 POST
  with a body → reset (exit 56/92) — only on vhosts whose realm sits in the regex context `exp:^/(?!api/)` (eye beta,
  iv-crm beta); old-style realm on `/` (test-site.beta) and live sites fine. Fix: `enableSpdy 0` in those beta vhosts
  (HTTP/1.1 only) — done on both betas (backups /root/*before-http1*, all other sites unchanged) and in `OPEN_API_PY`.
  Checked: browser-style POST 302, no password 401, API 200. Claude's code was right — it couldn't have found this
  (the request never reached the app). 408 checks, deployed.
- **2026-10-22 · Server Claude can now debug like I did (read-only web-server tools).** Change runs get `beta_probe`
  (curl with HTTP/1.1 vs 2, with/without the beta password, explains curl exit codes like 56/92), `beta_access_log` (did
  the request arrive?), `beta_webserver` (vhost + .htaccess + OLS messages, cert paths hidden); prompt tells Claude the
  order (app log → access log → protocol compare → web-server settings). Editing the web server itself (`beta_vhost_edit`
  with backup + auto-revert) was BLOCKED by the permission system ("Modify Shared Resources": ~20 sites on that server)
  — not built; Claude must send such a fix to the owner via report_problem. 411 checks, deployed.
- **2026-10-22 · App builds by hand + working QR (owner).** No more automatic app builds after a change / beta deploy
  (`mobile.auto_build` / `after_beta` removed): the Mobile apps panel highlights "Change #N changed the app code: …"
  with Build buttons (`mobile.needs_build` — touched, and no build since Claude's last edit, or the last one failed). QR
  codes didn't scan: Codemagic's public link is ~410 chars → too dense. Now the QR holds a 57-char public link
  `/d/<build id>/<hmac token>` (SECRET_KEY + build id + file link, no login, no-referrer/noindex/no-store, 404 when
  wrong / not ready / expired) that redirects to Codemagic; QR shown bigger (168px). Checked live: short link 302 →
  Codemagic → file. 416 checks, deployed.
- **2026-10-22 · Project page redesign, part 1 (owner: messy + long; Mobbin: Vercel / Railway / Cofounder / Gumloop).**
  Compact header (name + status, client/repo/tech/open counts, actions Sync / Beta deploy / Rollback / Go Live in the
  header — labels hidden under 420px), the 4 KPI tiles removed, Beta/Live as two slim cards (beta login behind "Locked —
  show login"), Mobile apps moved to a new **Apps** tab (dot when a change needs a build; builds as rows with Download +
  QR pop-over), change card: app-code highlight + Build buttons, "N files changed" folded; chat: only the last 6
  messages (older folded), Claude notes > 700 chars clamped with Show all; #section hashes open their tab.
  `summary > svg` sized too. Checked on a local copy (desktop + 375px, no sideways scroll). 416 checks, deployed.
  Deviation from mock-ui recorded: the project page no longer follows the mock 1:1. Next: chat with live steps +
  approval cards (part 2), Claude's wider powers (part 3).
- **2026-10-22 · Chat part 2 — like Claude's own chat.** Every step of a run is kept (`changes.agent_steps`, `_note`
  appends while working, local time) and shown LIVE in the working box (polled list); Claude's reply keeps them
  (`change_messages.steps`) as a folded "N steps". New MCP tool `ask_owner` (question / options / permission=true):
  creates a `ChangeQuestion` (migration 7fe1e74b2ac6), alert (bell + Telegram), card in the change (Approve / Deny or
  option buttons + own answer; permission = owner only), Claude WAITS up to 30 min (`beta_tools._wait_answer`), the
  answer is added to the chat; no answer → told not to do it. Every Claude Code change run now gets the tool session
  (ask_owner even without a beta). 422 checks, deployed.
- **2026-10-22 · Claude's powers, part 3.** Change runs: no `--strict-mcp-config` any more (the account's claude.ai
  connectors such as Mobbin may load) + `--permission-prompt-tool mcp__beta__permission_prompt`: any tool outside the
  allowed list becomes an owner card (Allow once / Allow for this run / Deny; remembered per run in job.context).
  Beta / release tool runs keep strict MCP. "Test Claude Code" now also lists the connectors the server Claude sees
  (`claude mcp list` as the runner, no model call). Root / server-wide actions by Claude NOT built — the permission
  system blocked it twice (vhost edit, root); Claude asks the owner with the exact command instead. CLI 2.1.301 accepts
  the flag (checked). 424 checks, deployed.
- **2026-10-22 · HANDOVER.md written** (owner: hand the whole chat to another Claude) — state, servers, projects, built,
  open items, hard rules; points to CLAUDE.md / MEMORY.md / TRACKER.md / CHANGES.md for detail.
- **2026-11-04 · Claude accounts with a web login (owner).** The setup-token is gone; every Claude Code run uses a
  Claude account the owner signs in through the web. Settings → Claude Code → "Claude accounts": add a name → the
  dashboard starts `claude auth login --claudeai` as `aided-runner` with that account's own folder
  (`<runner home>/claude-accounts/a<id>`, `CLAUDE_CONFIG_DIR` passed inside the sudo wrapper's `env`, so sudoers
  unchanged) → owner opens the claude.com link, pastes the code back → status / plan / masked e-mail / the account's
  own models + connectors are read (no model call). Every account's `projects/` links to the shared
  `~/.claude/projects`, so a project's session continues on any account. The server user's old web login became
  "Main account" (slug "", default). Chat: account picker + that account's models (+ effort). Staying logged in:
  `flask monitor` → `cc_logins.tick()` checks each account hourly, a run-out token gets one tiny Haiku call,
  logged out → one `claude_login` alert. Owner's choices: tool runs (beta / release / live steps) use the default
  account with web + connectors loaded (no `--strict-mcp-config`); beta setup as the beta website's user gets the
  default account's access token (renewed first); projects with a client's / developer's message → web + every
  connector call ask (cards); owner-only projects → connectors allowed without a card (`job.context.trusted`).
  Files: `app/cc_logins.py`, `app/models/claude_account.py`, migration `a1c3e5f7b9d2` (also blanks the old token),
  `claude_code.login_for`, `agent.py`, `beta_tools.permission_prompt`, `settings.py` account routes,
  `settings/index.html`, `projects/detail.html` + `project.js`, `cc_accounts.py` + `settings/claude.html`.
  Checked: 443 flow checks (fake CLI: add → wrong / right code → second account → pick per message → keep-alive →
  one logout alert → remove), unit tests 293 (1 Mac-only `stat` failure), browser on a DB copy with a fake CLI
  (desktop + 375px). Also: the Mac's `dashboard/` was 2 weeks behind the server — pulled the server source first.
  **Not deployed yet.** Next: deploy (then `aidash-code-guard save`), press Check now on Main account, add accounts.

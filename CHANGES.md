# CHANGES — "Zero manual work" roadmap (owner, 2026-10-17)

**Goal:** a person only (1) adds a project from GitHub, (2) fills its details once, (3) writes changes in the chat,
(4) approves. Everything else — beta website, its setup, database copy, any tech stack, git problems, web and mobile
builds, store uploads, Go Live, fixing failures — is done by the dashboard's Claude.

**What stays human (cannot be automated):** the one-time access the owner gives (GitHub, Play Console, Apple
Developer, Codemagic), approving a change, pressing Go Live with the authenticator code, and Google's / Apple's own
store review (their review takes hours to days and is not in our hands).

**Safety rules for every step** (the server also runs the Verixa CRM and ~15 client sites):
- Claude never gets an open root shell. Root-level actions go through **fixed, bounded tools** Claude calls
  (e.g. "make website for `x.beta.ivinfotech.com`", "copy live DB X into beta DB Y"); each tool checks its inputs
  (only `*.beta.ivinfotech.com`, only beta databases, live is only read). Claude decides *what* to do and *in which
  order*, and handles errors; the tools decide what is *allowed*.
- App-specific work (install, build, migrate) runs as the project's own website user, never root.
- Live changes only by switching to a fully-built release folder; if the site doesn't answer → automatic rollback.
- Secrets (payment, mail, cloud, signing keys) never go into a beta, a prompt or the dashboard's code.

Status legend: ⬜ to do · 🔶 in progress · ✅ done

---

## Step 1 — Beta privacy ✅ (2026-10-17)
Betas carry a copy of live data (Eye HMS = patient data) on a public address.
- Every automatic beta gets a password (HTTP basic auth via the beta's `.htaccess`; for app servers, at the proxy).
- The password is shown in the dashboard (project → Beta) and included with the client approval link.
- Test: beta asks for a password; with it the app works; `/.env` still 404.

## Step 2 — Claude's server tools (bounded) ✅ (2026-10-17)
Turn today's fixed provisioning into tools Claude calls during the beta setup:
| Tool | Allowed |
|---|---|
| `make_website(name)` | only `<name>.beta.ivinfotech.com`; CyberPanel website + Let's Encrypt SSL |
| `copy_database(engine, live, beta)` | MySQL (dump → own beta DB + user), **SQLite** (online `.backup` of the file), PostgreSQL; live read-only |
| `put_code()` | the beta branch from the dashboard's checkout (no GitHub key on the server) |
| `write_env(pairs)` | beta `.env` from live's; secrets blanked |
| `start_service(command, port)` | a backend (Flask, Django, FastAPI, Node, Next…) as the website's user: systemd unit on a free local port + proxy rule for `/` or `/api` |
| `run_as_site(cmd)` | any app command as the website's user (install, build, migrate) |
| `check_site(paths)` | smoke checks |
- Claude Code gets these as tools (MCP server or wrapper commands with sudo rules for exactly those scripts).
- Test: fake server tests for each tool's limits (wrong domain / live DB write / other site → refused).

## Step 3 — Beta starts by itself ✅ (2026-10-17)
- When project details are saved (live server + folder known) → beta setup starts automatically.
- Also when the first change is written and no beta exists (done today).
- Progress shown live: "Claude: making website… copying database… installing… building…".

## Step 4 — Any tech stack 🔶 (2026-10-17: Laravel ✅, React + Flask + SQLite ✅; Node/Express, Next.js, Django untested)
Runtimes on the server for website users: PHP (lsphp 8.1–8.5), Python (venv per project), **Node 20+** (today
only Node 16 for normal users — needs installing), Composer, npm/pnpm/yarn.
Templates Claude starts from (it adapts them per project):
- Static HTML · Laravel / PHP · Flask / Django / FastAPI · Node (Express / Next.js) · React/Vite SPA + API ·
  monorepos (frontend + backend folders).
- Test: Test Site (HTML), Eye HMS (Laravel), iv-crm (React + Flask + SQLite) all get a working beta with no hand work.

## Step 5 — Git problems solved by itself ✅ (2026-10-17)
- One repo per project (by GitHub repo id, so renames / transfers like `ivalema35 → ivronak` are followed).
- Servers never pull from GitHub: code is always pushed from the dashboard's checkout → origin / user mismatches
  can't break a deploy.
- Existing live folders that still pull from an old repo: detected and reported; on Go Live v2 (Step 6) live moves
  to release folders and no longer needs git at all.
- Repos owned by another person's account: the dashboard says exactly what to do (transfer or organization).

## Step 6 — Go Live v2 🔶 (started 2026-10-17)
1. Database snapshot (MySQL / SQLite / PostgreSQL).
2. New **release folder** built with the beta recipe (same steps as beta, live settings).
3. If a step fails → Claude fixes it **inside the release folder** (live keeps running), recipe updated.
4. Migrations (additive only) → switch live to the new release (instant) → smoke checks.
5. Site doesn't answer → automatic switch back (seconds) + alert. Old releases kept (last 3) for one-click rollback.
- First Go Live of an existing site converts it to the release layout (backup first, owner's OK per site).
- Done: engine + rehearsal (part 1), Claude fixes inside the new release (part 2), React + backend + SQLite layout
  like iv-crm (part 3, deployed; rehearsal on an iv-crm copy: convert + Go Live + rollback OK, 2026-10-21). Real sites: crm.ivinfotech.ca ✅ (2026-10-21);
  left: eyenosis.com and the rest, each with the owner's OK.

## Step 7 — Beta storage stays small ⬜
- Big upload folders are not copied (beta reads live's files read-only); small ones copied.
- A beta is removed (website, database, files) after its changes go live, or after N idle days; rebuilt on demand.
- Disk + database size per beta shown in the dashboard.

## Step 8 — Smoke tests per project ⬜
- Claude writes 5–10 checks per project at setup (home, login, key pages / APIs, assets load).
- Run after every beta deploy and every Go Live switch.

## Step 9 — Measuring accuracy (95 % gate) ⬜
- Every beta setup / beta deploy / Go Live records success without human help, or what failed.
- Dashboard page: success rate per step and per project. Target ≥ 95 % before adding more projects.

## Step 10 — Mobile apps (Flutter: Android + iOS) 🔶 (2026-10-22: Android test APK on Codemagic built; beta API for apps + iOS left)
- iOS builds need macOS → builds run on a cloud build service (**Codemagic** — Flutter-first, has Mac machines;
  or GitHub Actions). Not on the VPS (Linux, 2 CPU).
- Flow: change → Claude edits Flutter code → dashboard starts the cloud build →
  **mobile beta** = Play Store *Internal testing* (Android) + **TestFlight** (iOS) → client tests on phone →
  approve → Go Live = promote Android to production + submit iOS for Apple review.
- Signing keys live only in the build service's secure storage.
- Version numbers bumped automatically; backend changes must stay backward compatible with older app versions.
- One-time owner setup: Codemagic account, Play Console service account, App Store Connect API key, signing keys.

## Step 11 — Bring the existing projects over ⬜
- Eye HMS: beta ✅ (automatic, 2026-10-17); Go Live v2 + live folder conversion.
- iv-crm: automatic beta (SQLite + Flask service + React build), Go Live v2.
- Test Site: move to the managed beta.
- Eye HMS / other Flutter apps: Step 10.

---

## Owner's answers (2026-10-17)
1. **Beta password:** yes — every beta behind a password (a lock before the app's own login).
2. **Node 20+:** yes — install separately; existing sites keep their Node.
3. **Bounded tools:** yes. Claude must handle tool errors itself (retry, other settings, fix the app side) and never
   give up silently. A real bug in a tool: Claude writes the fix as a "Tool fix" change → owner approves with one
   click → Claude continues where it stopped (Claude never swaps a tool's code by itself — the tools are the limits).
   **Never mix projects:** own repo, branches, folder, Linux user, database, Claude session; tools refuse otherwise.
4. **Web server restart for a new beta:** standing permission (graceful, a few seconds); after each restart all
   other sites are checked automatically.
5. **Release folders on live sites: A — ask the owner before converting EACH live site (2026-10-17).** Was decided after a test on Test Site's beta (does OpenLiteSpeed follow the
   symlink switch). Site survey (2026-10-17): Laravel ×6, React+Flask ×1, Python/Node apps ×3, static ×3 — all can
   use releases if writable data (`.env`, storage / uploads, SQLite file) is shared outside the release. Left out:
   Verixa CRM (own deploy), ai.ivinfotech.com (third-party tool), the dashboard, the host's default site.
   Notes: goalfoundation.org.in is 13 GB (uploads to identify); eye.ivinfotech.com shares eyenosis.com's database and
   doesn't answer (owner to decide).
6. **Betas stay** (not deleted after Go Live) and are kept fresh automatically: new commits on main (checked every
   5 minutes) rebuild the beta; direct edits on a live server are detected (daily + before every deploy) and brought
   into GitHub as a "Found on live" change for approval — never silently overwritten; the beta database is refreshed
   from live when a new change starts or weekly. Only when free disk drops below 20 % the oldest idle beta database is
   removed (with an alert). Rebuilding a beta replays the saved recipe without Claude; Claude is called only when a
   step fails (max 3 fixes a day per project, then an alert).
7. **Mobile:** Codemagic. Owner has Google Play Console and Apple Developer accounts. A change gives a test build:
   Android APK (beta backend, separate app id "… (Test)", installs next to the live app) downloadable from the
   dashboard and the client link; iOS through TestFlight (first external build: short Apple review). Go Live =
   Play Store production / App Store review.


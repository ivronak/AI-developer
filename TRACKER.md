# TRACKER — AI Project Management Dashboard

## 1. Status

| Stage | What | Status |
| --- | --- | --- |
| Docs | Requirement, PRD, chat handover in `docs/` | Done |
| Mock UI — part 1 | S1 Login (password + OTP, 5-try lock) · S2 Projects home (cards, filters, search) | Done — design approved by owner 2026-10-14 |
| Mock UI — part 2 | S3 Project detail · S4 Chatbot / change · S5 Client approval · S6 Go Live / Rollback · S7 Backups · S8 Users & log | Built + tested in browser, waiting for owner's review |
| Real dashboard (v1, F01-F15) | After the full mock is approved | Pending |

### Mock UI map (`mock-ui/`)
| Page | Screens / features | Files |
| --- | --- | --- |
| `login.html` | S1 · F01 (password → OTP, 5 tries = 15-min lock, turned-off accounts refused) | `login.js`, `login.css` |
| `projects.html` | S2 · F04/F05 — Vercel-style overview: KPI tiles, compact Health / Activity log (by day) / Approvals (approve / reject in place) panels, project cards / list view toggle, search (F key), filters. Health: incident → red; stale backup / failing tests / approval 3+ days → yellow | `projects.js`, `projects.css` |
| `project.html?id=N` | S3 header + beta/live cards + Sync / Beta deploy / Go Live / Rollback · tabs Chat (S4, F06) · Changes · Deploys · Backups · Settings (F13 messages + payments) · S6 Go Live (approved changes, owner override with reason, OTP, snapshot first) + Rollback (OTP, optional DB snapshot; rolled-back changes return to beta) | `project.js`, `project.css` |
| `approve.html?t=TOKEN` | S5 · F09 — no login, checklist, Approve / Changes needed (+ screenshot), closes after reply or 7 days | `approve.js`, `approve.css` |
| `backups.html` | S7 · F12 — date-wise list, filters, Back up now, Restore to beta / live (live = owner + OTP) | `backups.js`, `backups-ui.js` (shared with the project Backups tab) |
| `users.html` | S8 · F02/F03 — owner only: add / edit user + project ticks, turn access off/on, activity log with person / project / date filters | `users.js`, `users.css` |
| shared | top bar (breadcrumb via `data-crumb` / `SHELL.setCrumb()`, icon navigation, Find, alerts, theme, user, sign out), phone tab bar, alerts panel (F14), sign-in guard | `shell.js`, `app.js` (state, dialogs, OTP, step runner), `data.js` (seed, `DATA_VERSION`) |

Mock state lives in the browser's localStorage (`apd.state`); "Reset demo data" on the login page restores the seed.

Outside the dashboard (owner's own work, PRD §10 "Preparation"): company GitHub org · access list · move all repos · pick 1-2 pilot projects — all pending.

## 2. Changes from the PRD
Everything decided after the PRD that changes what it says. The PRD stays the base; this list wins where they differ.

| Date | PRD said | Now | Why |
| --- | --- | --- | --- |
| 2026-10-14 | Interface in Hindi + English (§8) | English only | Owner's choice |

## 3. Decision Log

| Date | Decision | Why |
| --- | --- | --- |
| 2026-10-14 | Fresh project, folder `claude project/AI Project/`, git-ignored by the parent (Verixa) repo | Owner's choice; unrelated to Verixa / IV Goal |
| 2026-10-14 | Start with a clickable mock UI, 2 screens first (S1 + S2), then the rest | Owner's choice |
| 2026-10-14 | UI language: English only | Owner's choice (PRD said Hindi + English) |
| 2026-10-14 | Mock login: demo accounts Owner + Developer; Developer sees only assigned projects | Shows F02 in the mock |
| 2026-10-14 | New look: Vercel-style console (from Mobbin, owner asked for a more attractive UI) — monochrome ink buttons, left sidebar on desktop, Projects overview = Health / Waiting for client / Recent activity column + project cards (grid) or rows (list). References: mobbin.com/screens/a8d666ee-3303-4b28-9e1f-06b8dbd63cb6 (Vercel overview), mobbin.com/screens/d1dee152-f0ee-4fde-95b2-ff5dacc05125 (Vercel deployments list) | Owner didn't like the first look; Vercel fits a deploy / health dashboard best |
| 2026-10-14 | Projects page made colourful and layered (owner: too black & white, data not visible enough): KPI tiles on top (tap = filter), health counts as coloured tiles, each project has its own colour (header band + avatar), tech chips coloured, card layers = header · repo/stack · what's happening now · 4 number tiles · footer. Project colours avoid red/green so they never look like a status. Primary buttons now indigo | Owner feedback 2026-10-14 |
| 2026-10-14 | Sidebar removed (owner: looks dated). One top bar on every page: logo · breadcrumb (e.g. All Projects / Overview, All Projects / Parking Manager) · icon navigation pill (active item shows icon + name, others icon only with a hover label) · Find · Alerts · theme · user · sign out. Phones keep the bottom tab bar | Owner feedback 2026-10-14 |
| 2026-10-14 | Overview top = three compact panels side by side (Health · Activity log · Approvals), each with its own scroll, instead of one big Health block. Approvals panel: Pending (links out to clients) with Approve / Reject right there, and Approved (ready to Go Live → opens the project). Approve / Reject from the dashboard = the Owner recording the client's answer (e.g. given on a call), only with a written note, logged as "Approved for client" / "Changes for client". Developers see the list without the buttons | Owner feedback 2026-10-14 |
| 2026-10-14 | Same colourful design on every inner page: project page gets a coloured header in the project's colour (avatar, repo, tech chips, status) + status strip + 4 number tiles, coloured env cards, pill tabs, coloured chat; Backups gets number tiles + type icons; Users gets coloured user cards with project chips and an icon log grouped by day; client approval page gets the project header; login gets a soft colour background. Shared pieces live in `assets/css/ui.css`; project colours in `APP.hue()` | Owner: inner pages should match the dashboard |
| 2026-10-14 | Own git repo for this project → GitHub `ivronak/AI-developer` (SSH, dedicated key, `github-aidev` alias) | Owner created a fresh GitHub for it |
| 2026-10-14 | Go Live without client approval is allowed only for the Owner, only with a written reason (logged) | PRD §9: "Owner override सिर्फ़ कारण लिखकर" |
| 2026-10-14 | After a Rollback, the changes from that release go back to "In beta" | Requirement §4: error → rollback → debug → same flow from beta |
| 2026-10-14 | Developers can't Go Live / Rollback / restore live / turn beta messages "On" / use live payment keys | PRD §2 (Go Live only with Owner's permission) — safest default; per-developer Go Live permission can come later |
| 2026-10-14 | Clients have no login; the role selector in "Add user" offers Developer only (one Owner) | PRD §2: Client = one-time link only |
| 2026-10-14 | Health rules in the mock: red = site down / unexpected page change; yellow = backup older than 26 h, tests failing, or approval waiting 3+ days | PRD gives colours, not rules — reasonable default, owner can change |

## 4. Open Questions (PRD §11, not yet decided by the owner)
- Dashboard stack (suggested: Laravel) and hosting (suggested: separate small VPS).
- Backup storage (suggested: Cloudflare R2 / Backblaze B2).
- Pilot projects (suggested: active, Laravel, on VPS, not too critical).
- Alerts channel for v1 (suggested: Telegram bot or email).
- Product name for the dashboard (mock uses "AI Project Dashboard").

## 5. Session Log
- **2026-10-14** — Folder + docs set up. Built mock S1 Login and S2 Projects home in `mock-ui/`. Owner approved the design.
- **2026-10-14** — Built S3-S8: project page (chat, changes, deploys, backups, settings), client approval page, Go Live / Rollback dialogs, backups page, users & log page. Moved mock data into one shared state (`app.js`) and the top bar / tab bar into `shell.js`. Tested in the browser (desktop + phone, light + dark): new change → tests → send to client → client approves → Go Live with OTP → Rollback; backup now + restore live; add user + access off; developer limits (no Go Live, no unassigned project, no Users page). No console errors. Demo storage cleared after testing.
- **2026-10-14** — Owner (in Gujarati) asked for a more attractive dashboard + project list using Mobbin. Picked Vercel's projects overview + deployments list as the reference. Re-themed all pages (monochrome tokens, links blue), new sidebar shell, rebuilt the Projects overview (health / waiting / activity column, cards with repo chip + status ring, list view). Checked desktop 1440 + phone 375, light + dark, all pages, no console errors.
- **2026-10-14** — Owner: page too plain, wanted colourful layered cards. Added KPI row, coloured health tiles, per-project colour layers on cards, coloured tech chips and activity icons, indigo accent; fixed a phone overflow from the filter tabs. Checked desktop + phone, light + dark, no console errors.
- **2026-10-14** — Replaced the desktop sidebar with a top bar: breadcrumb + icon navigation where the old "All Projects / Overview" text was. Project page shows its name in the breadcrumb (old back link removed). Checked desktop + phone, all pages, no console errors.
- **2026-10-14** — Health block was too big. Made it compact and put it beside a new Activity log (grouped by day) and a new Approvals panel (approve / reject without opening the project). Tested approve + reject (note required, KPIs and log update), desktop + phone, no console errors; demo data reset afterwards.
- **2026-10-14** — Owner OK'd the dashboard; asked for the same look inside. Restyled project, backups, users, approval and login pages; moved shared components to `ui.css`. Checked desktop + phone, no overflow, no console errors.
- **2026-10-14** — Built `mock-ui/tour.html`: a ~80-second guided tour for explaining the project to the developer. Start → Hindi voice (Mac "Lekha", clips in `mock-ui/tour/s1..s6.m4a`, text in `s1..s6.txt`) + Hindi captions + a fake mouse that clicks through the real mock (overview → project → beta/live → chat change → send to client → client approves → Go Live with OTP → Rollback). Wording is about making the developer's work easier — no replacement talk. Resets demo data at start. Owner didn't like the Mac voice; to swap voices replace the six .m4a files (same names). Tested end to end in the browser, no console errors.
- **2026-10-14** — Connected to GitHub: `git init` (main), dedicated SSH key, first commit. Push waits for the owner to add the public key on GitHub.

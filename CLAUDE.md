# CLAUDE.md — AI Project Management Dashboard

A **fresh, separate project**. It has nothing to do with the Verixa CRM/website (parent folder) or IV Goal (`../ivgoal`). None of their rules, code, design or data apply here — only this file does.

## 0. These two files are Claude's own
`CLAUDE.md` (rules) and `TRACKER.md` (memory: what is done, what is pending, every decision that changed the PRD) are **written and managed by Claude**. The owner should never have to ask for them to be updated. **After every change and every piece of work, update both** (TRACKER always; CLAUDE.md whenever a working rule changes or a lesson is learned).

## 1. Start of every request — in this order
1. Read **this file** (all rules).
2. Read **`TRACKER.md`** — modules done / pending, decisions, open questions, last session.
3. Read the **PRD** `docs/PRD-AI-Project-Management-Dashboard.md` — what to build (F01-F27, S1-S16, data model, acceptance checks). **Source of truth.** Background when needed: `docs/AI-Project-Management-Requirement.md` (why / how) and `docs/00-Chat-Handover-Context.md` (decisions from the earlier chat — don't reopen them unless the owner asks).
4. Look at the **codebase** (what really exists in `mock-ui/` and later the app folder). If TRACKER and the code disagree, trust the code and fix TRACKER.
5. Decide the next step, tell the owner in one or two lines, then work — **step by step, phase by phase**, never skipping ahead of a phase the owner hasn't approved. If there is a question only the owner can answer, ask first (owner, 2026-10-14).

## 1b. End of every piece of work
- `TRACKER.md`: module status table, Decision Log (especially any change from the PRD — what, when, why), Open Questions, Session Log.
- PRD: update the feature status column (§4) when a feature changes state; if a requirement changes, record it in TRACKER's "Changes from the PRD" too.
- `CLAUDE.md`: any new rule or lesson.

## 2. Communication
- The owner writes Hindi by voice (spelling may be off — read for meaning). Reply in **simple Hindi (Devanagari)**, technical terms in English, short: what changed, what is next.
- Docs and code in **English**. The dashboard UI is **English only** (owner, 2026-10-14 — overrides the PRD's "Hindi + English" line).
- **Git (2026-10-14):** this folder is its own repo (branch `main`), remote `origin` = `git@github-aidev:ivronak/AI-developer.git` (https://github.com/ivronak/AI-developer). SSH uses the dedicated key `~/.ssh/aidev_github_ed25519` via the `github-aidev` host alias in `~/.ssh/config` — never ask for or use a password/token. Commit and push only when the owner asks. The parent Verixa repo git-ignores this folder.
- Nothing on any live server (VPS / subdomain) without the owner's explicit OK.

## 3. Approach
- **Step 1 = clickable mock UI** in `mock-ui/` — plain HTML + CSS + vanilla JS, mock data, no backend, no framework, no build step. First S1 (Login) + S2 (Projects home) for design approval, then S3-S8.
- The real dashboard comes after mock approval. Planned stack (PRD §11 suggestion, not yet confirmed by the owner): Laravel on a separate small VPS.
- Mobile first: everything must work at 360px wide, big tap targets. Light + dark theme.
- Use generic sample data only (Client A, Parking Manager, ...). No real client names.

## 4. Mock UI rules
- **Design reference: Vercel's console** (picked from Mobbin, 2026-10-14) — monochrome surfaces, ink-coloured primary buttons, colour only for status (ok / warn / bad / info), Geist + Geist Mono, thin 1px borders, **no sidebar** — one top bar with breadcrumb + icon navigation (owner, 2026-10-14). New screens follow it. Colour comes from per-item hues (`--h` + the `--band/--tint/--solid/--ink` layer tokens in `tokens.css`, which already have dark values) — never hard-code a hex in a component; keep item hues away from red/green so they never read as a status.
- CSS load order: `tokens.css` → `base.css` → `app.css` (shell) → `ui.css` (shared colourful components: KPI tiles, hero band, avatars, chips, stat tiles, status strip, row icons, activity list) → page css. Reuse `ui.css` pieces on new screens; project colour = `APP.hue(p)`, tech colour = `APP.techHue(t)`. All colors are tokens in `tokens.css`; no literal colors in components. No `@layer`, no regex lookbehind in JS (older browsers drop the whole file).
- New signed-in page checklist: `<body data-page="...">` (+ `data-owner-only` for Owner pages), scripts in this order with `defer`: `data.js` → `app.js` → `shell.js` → (`backups-ui.js` if needed) → page script. The page script starts with `if (!window.SHELL || window.SHELL.blocked) return;`. `shell.js` draws the top bar (breadcrumb + icon nav) / alerts / phone tab bar — never hand-write those in a page. Set the breadcrumb with `<body data-crumb="...">` or `SHELL.setCrumb(text)`. Add the page to the nav in `shell.js` if it needs a menu entry.
- All mock data goes through `APP.S()` (localStorage `apd.state`) and `APP.save()`. Changing the seed's shape in `data.js` → bump `DATA_VERSION`, or old browsers keep stale state. Every user action that changes something calls `APP.log(...)` (F03).
- Dialogs: use `APP.dialog(...)` (closes only from ✕ / Cancel when it has fields). Go Live / Rollback / Restore-live always need `APP.otpHtml()` + `APP.otpOk()` and are owner-only.
- Syntax-check JS after every edit: `/usr/bin/osascript -l JavaScript -e 'ObjC.import("Foundation"); ["data.js","app.js","shell.js","login.js","projects.js","backups-ui.js","project.js","approve.js","backups.js","users.js"].forEach(function(f){ var s=$.NSString.stringWithContentsOfFileEncodingError("/Users/rj/Desktop/claude project/AI Project/mock-ui/assets/js/"+f,4,null).js; try { new Function(s); console.log(f+" OK"); } catch(e){ console.log(f+" ERROR: "+e.message);} })'` (add new files to the list).
- Local preview (port 5180, no-cache headers so the browser never shows stale files), run in background with Bash:
  ```
  cd "/Users/rj/Desktop/claude project/AI Project/mock-ui" && ruby -rwebrick -e 'root=Dir.pwd
  class NC < WEBrick::HTTPServlet::FileHandler
    def do_GET(req, res); super; res["Cache-Control"] = "no-store"; end
  end
  s = WEBrick::HTTPServer.new(Port: 5180, DocumentRoot: root, AccessLog: [], Logger: WEBrick::Log.new($stderr, WEBrick::Log::WARN))
  s.mount("/", NC, root)
  trap("INT"){s.shutdown}; trap("TERM"){s.shutdown}
  s.start'
  ```
  URL: `http://localhost:5180/login.html`. Test every flow in the browser pane before saying it is done; clear demo localStorage/sessionStorage afterwards.

# AI Project Dashboard ("aided") — UI / Design / UX redesign plan

Status: PLAN ONLY (2026-11-18). No code changed yet. Waiting for owner's OK on the open questions at the bottom.

## 0. Iron rule (owner, non-negotiable)
Only **UI, design, UX** change. Logic, routes, forms' field names/ids that JS or tests use, DB, jobs, deploy/GitHub/Claude flows, permissions, security: **untouched**.
Practical enforcement:
- Edit only `templates/**` markup/classes, `static/css/**`, `static/js/**` (presentation behaviour only), and new static assets. Never edit `blueprints/*.py`, `models`, `golive.py`, `deploy.py`, `agent.py`, `cc_logins.py`, `alerts.py`, `health.py`, migrations.
- Keep every `id`, `name`, `data-*`, `form action`, CSRF field, and JS hook that `project.js` / `projects.js` / `ui.js` / `tests/flows.py` rely on. Restyle by adding classes, not renaming hooks.
- Gate for every step: `tests/flows.py` must stay green (524 checks now) + diff review shows no `.py` change except none. A CI-style check: `git diff --name-only` must list only `templates/`, `static/`, `docs/`.
- Backup UI stays hidden (owner's earlier decision).
- Each step is a separate small commit, deployable and revertable on its own.

## 1. Where we are today (audit, from code)
- ~1,900 lines CSS+JS, 10 CSS files, vanilla JS, no build step, Jinja templates. Pages: login/setup/codes/account, projects list, project detail (Chat / Changes / Apps / Deploys / Settings tabs, 659-line template), settings (325), claude accounts, users, AI cost, client approve page.
- Look is "Vercel-lite": monochrome + indigo accent, but inconsistent: per-project hue layers, custom blocks per page, spacing/type not on one scale.
- **Motion is almost absent**: only ~12 `transition` references in all CSS, no entrance/exit, no skeletons, no toasts system, no reduced-motion guard.
- Geist font is loaded from Google Fonts (render-blocking third party; flash of fallback font).
- No command palette / keyboard shortcuts (only Enter-to-send, Esc).
- Detail page is long and dense: status, change card, chat, composer, tabs all compete. Many one-off components (cards, chips, pills) rather than one system.
- Mobile: top bar + bottom tab bar exist, but forms/tables are desktop-first.

## 2. Research findings (what "million-dollar" products do)
Mobbin references reviewed (all web unless noted):
- Project list / overview: [Vercel](https://mobbin.com/screens/8bd7da13-fd91-4197-b8c3-f49a21d026bc), [Linear projects](https://mobbin.com/screens/3d3a4fae-d824-440a-961b-001c6b715a4a), [Plane](https://mobbin.com/screens/664e73a8-005f-4b2a-8c23-45931928391d)
- Project / deployments page: [Vercel production deployment](https://mobbin.com/screens/d8e63884-5d43-4337-bf31-5667bc4ba5db), [Cloudflare versions](https://mobbin.com/screens/2dfb1cd3-26ac-4e63-9866-f293a0306177), [Render events](https://mobbin.com/screens/981fad00-53c2-4307-aa11-9bae7ae4b8ed), [Railway](https://mobbin.com/screens/8907c9da-f513-4722-9f19-826602c11429), [PlanetScale deploy requests](https://mobbin.com/screens/be0ec4c5-4a49-4f4a-8a29-179143a514c5), [Laravel Cloud](https://mobbin.com/screens/18ef5450-d098-4785-9c12-cb014e1845cc)
- AI chat + composer + model picker: [Claude](https://mobbin.com/screens/e8c0af18-6e3a-4273-93c1-935e958ff8b3), [Devin](https://mobbin.com/screens/3563ed9d-b08f-4d5d-b8a4-9204bd55ff83), [Cursor](https://mobbin.com/screens/cad88e0e-78cd-472c-8f8a-af81b40236b9), [Gumloop model menu](https://mobbin.com/screens/a3f8b130-cfc5-4ddc-8fd9-b95a4a6927bf)
- Review / approve / merge: [Graphite PR](https://mobbin.com/screens/43724524-d851-43d4-8272-721c43f8f032), [Devin review](https://mobbin.com/screens/aeaed0d8-a1c1-4094-8615-c34f45cc7c45), [Replit review changes](https://mobbin.com/screens/e92dd0ec-6660-4610-930a-38900c351734), [ChatGPT codex diff](https://mobbin.com/screens/652d7e4c-c722-49ef-a46e-172935e49c7d), [GitLab MR](https://mobbin.com/screens/a213fd4c-2f4a-409f-a639-295f8a0dfc99)
- Mobile (iOS): [v0 deployment sheet](https://mobbin.com/screens/23c0fcc1-8d7d-4a13-9da2-71cfc1a335f0), [Shopify floating tab bar](https://mobbin.com/screens/564aeba3-96e1-4e4e-9cac-85f522552a68)

Patterns that repeat across the best ones:
1. **One calm shell**: slim left rail + a top context bar (workspace / project / environment breadcrumb) + a global search/command box (`⌘K`). Content max-width ~1100–1200px, lots of whitespace.
2. **Status = one visual language**: small dot + label pill, same colours everywhere (green live, amber warn, red failed, blue running). Colour only for meaning.
3. **Project page = "hero status" first**: Vercel/Render show current live version, domain, last deploy, and the one primary action in the first screen; history is a quiet list below. Rollback is a secondary, clearly-labelled action.
4. **Lists as tables with hover rows** (Linear/Cloudflare): hash in mono, relative time, author avatar, `⋯` row menu. Cards only for project grid.
5. **Review screen = main column + right rail** (Graphite/Devin/GitLab): left = story/conversation/diff, right = checks, approvals, actions. Single sticky primary button ("Approve"/"Go Live").
6. **AI chat = centered column, composer as one rounded card**, model picker as a compact menu with one-line descriptions (Devin/Gumloop), `+` for attachments, send button inside the card.
7. **Feedback everywhere**: toasts, inline progress, skeleton loading, optimistic states; empty states with one clear next action.
8. **Dark mode is a first-class theme** for dev tools (token-driven, not an afterthought).
9. Mobile = bottom nav (floating pill in Shopify), sheets for detail, big tap targets.

Web research (secondhand sources, treat as conventions, not standards): dashboards lead with 3–5 key numbers, progressive disclosure, tables over charts, command palette (Linear is the reference), dark-first for power tools; motion: 120–200ms micro-interactions, 180–260ms component transitions, 240–400ms for layout/route; ease-out (quart/expo) curves, never ease-in for UI; animate only `transform` + `opacity`; always honour `prefers-reduced-motion`; use `@starting-style` + `transition-behavior: allow-discrete` for enter/exit of popovers/dialogs; View Transitions API for tab/page changes; CSS scroll-driven animations for light reveals; Motion (motion.dev, vanilla build ~few KB) only if we need real spring physics.

### Motion research — Mobbin limits and how we cover it
The Mobbin MCP returns still images only (no video/animation). Substitutes, in order of effort:
1. Study motion from the products themselves with Playwright (Chromium is installed): open public pages / free-tier demos of Vercel, Linear, Railway marketing+docs, record `video` + trace, and read their actual CSS (`transition`, `@keyframes`, easing curves, durations) via DevTools protocol. This gives real numbers instead of guesses.
2. Public references (to browse, not scrape): Mobbin site itself (it now has animated captures), Ripplix, Appealing, 60fps, Codrops Motion Highlights, Landing Love.
3. Our own motion spec (section 4) built from the conventions above, then tuned by eye in the browser.
Owner action needed only if he wants us to log in to a paid reference site.

## 3. Design direction ("Calm precision")
Keep the Vercel/Linear DNA, make it intentional:
- **Color**: neutral grayscale (12-step scale light+dark), ONE brand accent (keep indigo family), semantic status colours only for state. Remove decorative per-project hue from big surfaces; keep it as a tiny avatar/stripe so projects stay recognisable.
- **Type**: Geist + Geist Mono, self-hosted (woff2 in `static/fonts`, `font-display: swap`, preload). Scale: 12 / 13 / 14 (base) / 16 / 20 / 24 / 32, line-heights 1.4–1.6, tabular numbers for all metrics/times, mono for hashes/domains.
- **Space/shape**: 4px grid (4 8 12 16 24 32 48 64), radii 6 / 10 / 14, one border colour, shadow only on floating layers (menus, dialogs). Surfaces: bg → surface → raised.
- **Icons**: one consistent 1.5px stroke set (the existing inline icon helper, audit and normalise sizes 16/20).
- **Density**: comfortable default; compact table rows (36px) for lists.
- **Theming**: tokens in `tokens.css` only; light/dark/auto toggle already supported by `data-theme`; verify contrast AA in both.

## 4. Motion system (tokens + rules)
- Tokens: `--dur-fast 120ms`, `--dur-base 180ms`, `--dur-slow 260ms`, `--dur-page 320ms`; `--ease-out cubic-bezier(.22,1,.36,1)`, `--ease-in-out cubic-bezier(.65,0,.35,1)`; optional `--spring` via `linear()` easing (CSS-only spring).
- Where motion is used (and nowhere else): button/row hover + press (scale .98), focus ring, tab underline slide, popover/menu/dialog enter-exit (opacity+translate 4–8px), toast slide-in, list item insert (height/opacity), skeleton shimmer while loading, status dot pulse for "running", progress bar for jobs, chat message arrive, card hover lift (1–2px), page/tab cross-fade via View Transitions with graceful fallback.
- Guard: `@media (prefers-reduced-motion: reduce)` → transitions ≈ 0, no movement, keep state change.
- Budget: no layout-property animation, no animation > 400ms, no infinite animation except "running" indicators.

## 5. Information architecture / UX changes (presentation only)
1. **App shell**: persistent left rail (collapsible, icon+label) on desktop; top context bar with breadcrumb `Projects / <project> / <tab>`, global search, theme toggle, account menu, Claude usage chip. Phone: floating bottom nav + sheets.
2. **Command palette (⌘K / Ctrl+K)**: jump to project, tab, settings page; purely client-side over links already in the page (no new backend).
3. **Projects page**: header (title, search, filter chips, grid/list toggle), summary strip (needs attention / waiting approval / live / total) from data already rendered, project cards redesigned (avatar, name, status pill, live version + domain, last deploy, open/waiting counts, `⋯`), table view with sortable-looking columns. Real empty + loading + "no results" states.
4. **Project detail**: top "hero" = name, client, status pill, live URL, live commit, last deploy, primary action area; tabs become a clean underline tab bar (sticky). Chat tab keeps the layout the owner approved (card on top, bordered thread box, composer below) but polished. Changes tab = table/list with status pills + filter. Deploys tab = timeline list (Render/Cloudflare style). Settings tab = grouped sections with a left sub-nav on desktop.
5. **Change review**: two-column (conversation/diff/results | checks + approvals + actions), sticky action bar, destructive actions visually separated and confirmed in a proper dialog (replace `confirm()` styling only — same form posts).
6. **Composer + model/effort picker**: finalise as the single rounded card; menu with one-line descriptions, keyboard navigable, remembers nothing new (no logic change).
7. **Settings / Users / AI cost / Claude accounts**: same page header, card sections, form components, tables; AI cost gets proper stat tiles + a clean table (no new charts unless trivial inline SVG sparkline from existing numbers).
8. **Login / setup / 2FA code**: centered card on subtle gradient/grid background, brand mark, clear errors, password show/hide, large OTP boxes.
9. **Client approve page** (external client sees it): the most "brand-facing" page; clean summary, big Approve / Request changes buttons, trust cues, fully mobile-first.
10. **System feedback**: unified toast + inline alert + banner components; skeletons for slow panels; consistent empty states with one action; friendly error pages (404/500 styled).
11. **Accessibility**: visible focus, 40–44px targets, AA contrast, labels, `aria-*` kept/added, keyboard-operable menus/tabs, skip link.
12. **Performance**: self-hosted fonts, no new frameworks, keep vanilla JS, CSS < ~60KB total, no layout shift, `content-visibility` on long lists.

## 6. Technical approach (no logic touched)
- Stay on Flask + Jinja + vanilla CSS/JS. **No React/Tailwind/build step** (would risk logic and deploy flow).
- Reorganise CSS into layers: `tokens.css` → `base.css` (reset, type) → `components.css` (button, input, pill, card, table, menu, dialog, toast, tabs, skeleton) → page files. No `@layer`, no regex lookbehind (CLAUDE.md §5: older browsers drop the whole file) — keep specificity low with single-class selectors. Replace one-off rules page by page.
- Shared Jinja macros for repeated pieces (status pill, page header, empty state, stat tile) in `templates/_ui.html` — pure markup.
- JS additions are small, separate files (`palette.js`, `toast.js`, `motion.js`); existing files only get presentation tweaks.
- A visual QA harness: Playwright script (local seeded instance from the test fixtures, never production data) that screenshots every page at 375 / 768 / 1280, light + dark, saved to the scratchpad for before/after review. Owner also reviews live with screenshots like before.
- Safety net: each phase deploys with the existing manual procedure + code-guard; rollback = redeploy previous commit. DB backup before each deploy as always.

## 7. Phases (each = reviewable, shippable, small commits)
- **P0 — Foundations (invisible-ish)**: self-host fonts, tokens v2 (colour/type/space/radius/shadow/motion), reduced-motion guard, component CSS (button, input, pill, card, table, menu, dialog, toast, skeleton). Gate: tests green, pages look equal or better.
- **P1 — Shell + navigation**: left rail / top bar / mobile nav, breadcrumb, global search + ⌘K palette, theme toggle polish.
- **P2 — Projects page**: summary strip, cards, table view, empty/loading states.
- **P3 — Project detail core**: hero header, tab bar, Chat polish, Changes list, Deploys timeline.
- **P4 — Change review & approval surfaces** (incl. client approve page): two-column review, action bar, dialogs.
- **P5 — Settings, Claude accounts, Users, AI cost, Account**: unified forms/tables.
- **P6 — Auth pages + error pages + empty states + toasts everywhere.**
- **P7 — Motion pass + a11y pass + mobile pass + perf**: tune durations by eye, View Transitions, skeletons, QA across devices; final consistency sweep.
Order rationale: foundations first so every later page is cheap; P2/P3 are what the owner sees daily; client page (P4) is the external face.

## 8. Definition of done / quality bar
- Every page passes: 375/768/1280, light+dark, keyboard-only, reduced-motion, AA contrast, no horizontal scroll, Lighthouse a11y ≥ 95 / perf ≥ 90 on the projects page.
- `tests/flows.py` green, zero `.py` logic diff, no secrets, health 200 after each deploy.
- Owner sign-off by screenshots per phase before the next starts.

## 9. Open questions for owner (answers change the plan)
1. Visual taste: stay **Vercel/Linear minimal (recommended)**, or more expressive (gradients, glass, bolder brand colour)?
2. Dark mode as default, or follow the device (current)?
3. Navigation: CLAUDE.md records "no sidebar, one top bar" (2026-10-14) but the current CSS already has a left sidebar on desktop — which one is the rule now? (Recommended: keep the current left sidebar, polished.)
4. Is the **client approve page** allowed to carry a brand/logo/colour per client?
5. Which phase first? (Recommended: P0 → P1 → P2.) Or start with only the Projects page + Project detail to see the new look fast?
6. OK to self-host Geist fonts (download from the official repo/Google Fonts) and to add a small motion library only if CSS is not enough?
7. Credits: owner said the weekly limit is almost finished — do P0–P2 first and the rest in later weeks?

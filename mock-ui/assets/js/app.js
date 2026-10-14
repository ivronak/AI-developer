/* Shared helpers: icons, theme, toasts, mock session, mock state (localStorage), dialogs, step runner. */
(function () {
  var D = window.DEMO;
  var ICONS = {
    sun: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
    moon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>',
    eye: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>',
    eyeOff: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3l18 18M10.6 5.1A10 10 0 0 1 12 5c6.4 0 10 7 10 7a17 17 0 0 1-3.2 4.1M6.6 6.6A17 17 0 0 0 2 12s3.6 7 10 7a9.6 9.6 0 0 0 5.4-1.6M9.9 9.9a3 3 0 0 0 4.2 4.2"/></svg>',
    bell: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10.3 21a1.9 1.9 0 0 0 3.4 0"/></svg>',
    search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>',
    grid: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>',
    db: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/></svg>',
    users: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="8" r="4"/><path d="M2 21a7 7 0 0 1 14 0M16 4a4 4 0 0 1 0 8M22 21a7 7 0 0 0-4-6.3"/></svg>',
    logout: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/></svg>',
    back: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 18l-6-6 6-6"/></svg>',
    send: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2L11 13M22 2l-7 20-4-9-9-4z"/></svg>',
    clip: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5l-8.5 8.5a5.5 5.5 0 0 1-7.8-7.8l9-9a3.7 3.7 0 0 1 5.2 5.2l-9 9a1.8 1.8 0 0 1-2.6-2.6l8.5-8.5"/></svg>',
    copy: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1"/></svg>',
    sync: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 0 1-15.5 6.3L3 16M3 12a9 9 0 0 1 15.5-6.3L21 8M21 3v5h-5M3 21v-5h5"/></svg>',
    upload: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 16V4M7 9l5-5 5 5M4 16v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/></svg>',
    rocket: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 15c-1.5 1.3-2 5-2 5s3.7-.5 5-2c.7-.8.7-2.1-.1-2.9a2.2 2.2 0 0 0-2.9-.1zM12 15l-3-3a22 22 0 0 1 2-4A12.9 12.9 0 0 1 22 2c0 2.7-.8 7.5-6 11a22 22 0 0 1-4 2zM9 12H4s.5-3 2-4c1.6-1.1 5 0 5 0M12 15v5s3-.5 4-2c1.1-1.6 0-5 0-5"/></svg>',
    undo: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7v6h6"/><path d="M21 17a9 9 0 0 0-15-6.7L3 13"/></svg>',
    check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>',
    x: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg>',
    plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>',
    github: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 .5a12 12 0 0 0-3.8 23.4c.6.1.8-.3.8-.6v-2.2c-3.3.7-4-1.4-4-1.4-.6-1.4-1.4-1.8-1.4-1.8-1.1-.7.1-.7.1-.7 1.2.1 1.8 1.2 1.8 1.2 1.1 1.8 2.8 1.3 3.5 1 .1-.8.4-1.3.8-1.6-2.7-.3-5.5-1.3-5.5-5.9 0-1.3.5-2.4 1.2-3.2-.1-.3-.5-1.5.1-3.2 0 0 1-.3 3.3 1.2a11.5 11.5 0 0 1 6 0C17.3 4.6 18.3 5 18.3 5c.7 1.7.2 2.9.1 3.2.8.8 1.2 1.9 1.2 3.2 0 4.6-2.8 5.6-5.5 5.9.4.4.8 1.1.8 2.2v3.3c0 .3.2.7.8.6A12 12 0 0 0 12 .5z"/></svg>',
    branch: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="5" r="2"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="7" r="2"/><path d="M6 7v10M18 9c0 5-6 4-12 8"/></svg>',
    list: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M8 6h13M8 12h13M8 18h13M3.5 6h.01M3.5 12h.01M3.5 18h.01"/></svg>',
    activity: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>',
    ext: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6M15 3h6v6M10 14L21 3"/></svg>'
  };

  function store(kind) {
    try { return kind === "session" ? window.sessionStorage : window.localStorage; } catch (e) { return null; }
  }
  function get(key, kind) {
    var s = store(kind); if (!s) return null;
    try { return JSON.parse(s.getItem(key)); } catch (e) { return null; }
  }
  function set(key, val, kind) {
    var s = store(kind); if (!s) return;
    try { if (val === null) s.removeItem(key); else s.setItem(key, JSON.stringify(val)); } catch (e) {}
  }

  /* ---------- theme ---------- */
  function isDark() {
    var t = document.documentElement.getAttribute("data-theme");
    if (t) return t === "dark";
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  }
  function paintThemeButtons() {
    var btns = document.querySelectorAll("[data-theme-toggle]");
    for (var i = 0; i < btns.length; i++) btns[i].innerHTML = isDark() ? ICONS.sun : ICONS.moon;
  }
  function toggleTheme() {
    var next = isDark() ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem("apd.theme", next); } catch (e) {}
    paintThemeButtons();
  }

  /* ---------- small helpers ---------- */
  function toast(text) {
    var box = document.getElementById("toasts"); if (!box) return;
    var el = document.createElement("div");
    el.className = "toast"; el.textContent = text;
    box.appendChild(el);
    setTimeout(function () { el.remove(); }, 3400);
  }
  function agoMin(min) {
    if (min < 1) return "just now";
    if (min < 60) return min + " min ago";
    var h = Math.round(min / 60);
    if (h < 24) return h + " h ago";
    var d = Math.round(min / 1440);
    if (d < 30) return d + (d === 1 ? " day ago" : " days ago");
    var m = Math.round(d / 30);
    return m + (m === 1 ? " month ago" : " months ago");
  }
  function ago(ts) { return agoMin(Math.max(0, Math.round((Date.now() - ts) / 60000))); }
  function minSince(ts) { return Math.round((Date.now() - ts) / 60000); }
  function stamp(ts) {
    var d = new Date(ts);
    var mo = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][d.getMonth()];
    var h = d.getHours(), m = d.getMinutes();
    return d.getDate() + " " + mo + ", " + (h < 10 ? "0" : "") + h + ":" + (m < 10 ? "0" : "") + m;
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  /* Each project gets its own colour (identity only — status keeps red / amber / green). */
  var HUES = [262, 199, 320, 180, 222, 290, 28, 245, 305, 172, 210, 275];
  var TECH_HUE = { Laravel: 22, Flutter: 205, Flask: 170 };
  function hue(p) { return HUES[(p.id - 1) % HUES.length]; }
  function techHue(t) { return TECH_HUE[t] || 240; }
  function initials(name) { return String(name).split(" ").map(function (w) { return w.charAt(0); }).join("").slice(0, 2).toUpperCase(); }
  function slug(s) { return s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, ""); }
  function hex(seed) {
    var x = (seed * 2654435761) >>> 0, out = "";
    for (var i = 0; i < 7; i++) { x = (x * 1103515245 + 12345) >>> 0; out += "0123456789abcdef".charAt(x >>> 28); }
    return out;
  }
  function token() {
    var c = "abcdefghjkmnpqrstuvwxyz23456789", t = "";
    for (var i = 0; i < 20; i++) t += c.charAt(Math.floor(Math.random() * c.length));
    return t;
  }
  function qs(name) {
    var m = new RegExp("[?&]" + name + "=([^&]*)").exec(window.location.search);
    return m ? decodeURIComponent(m[1]) : null;
  }

  /* ---------- session ---------- */
  function currentUser() { return get("apd.user", "session"); }
  function signIn(user) { set("apd.user", user, "session"); }
  function signOut() { set("apd.user", null, "session"); window.location.href = "login.html"; }

  /* ---------- mock state ---------- */
  function build() {
    var now = Date.now(), M = 60000, s = { v: D.DATA_VERSION, seq: 1000 };
    function ts(min) { return now - min * M; }
    s.users = D.users.map(function (u) {
      return { id: u.id, name: u.name, email: u.email, mobile: u.mobile, role: u.role, projects: u.projects, active: u.active, lastAt: ts(u.lastMin) };
    });
    s.projects = D.projects.map(function (p) {
      var sl = slug(p.name);
      return {
        id: p.id, name: p.name, client: p.client, tech: p.tech, host: p.host, active: p.active, incident: p.incident,
        repo: "company-org/" + sl, server: p.host === "VPS" ? "vps-2.company.test" : "cpanel.client-host.test",
        beta: { url: "https://beta." + sl + ".example.com", commit: hex(p.id * 7 + 1), msgMode: "off", testNumbers: "+91 98765 44821", payMode: "sandbox", syncAt: ts(p.deployMin + 60) },
        live: { url: "https://" + sl + ".example.com", commit: hex(p.id * 7) }
      };
    });
    s.deployments = []; s.backups = [];
    D.projects.forEach(function (p) {
      [0, 4320, 10080].forEach(function (extra, k) {
        var at = ts(p.deployMin + extra);
        s.backups.push({ id: s.seq++, projectId: p.id, type: "snapshot", at: at - M, sizeMb: 40 + (p.id * 37) % 600, ok: true });
        s.deployments.push({ id: s.seq++, projectId: p.id, env: "live", commit: k === 0 ? hex(p.id * 7) : hex(p.id * 7 + 20 + k), at: at, by: 1, note: k === 0 ? "Latest release" : "Earlier release", rolledBack: false });
      });
      for (var day = 0; day < 6; day++) {
        var at = ts(p.backupMin + day * 1440);
        s.backups.push({ id: s.seq++, projectId: p.id, type: "db", at: at, sizeMb: 40 + (p.id * 37) % 600 - day, ok: true });
        s.backups.push({ id: s.seq++, projectId: p.id, type: "files", at: at + 5 * M, sizeMb: 300 + (p.id * 113) % 2400, ok: true });
      }
      for (var f = 0; f < (p.backupFails || 0); f++) {
        s.backups.push({ id: s.seq++, projectId: p.id, type: "db", at: ts(120 + f * 1440), sizeMb: 0, ok: false, error: "Could not reach the database server" });
      }
    });
    s.changes = D.changes.map(function (c) {
      var x = {
        id: c.id, projectId: c.projectId, title: c.title, status: c.status, at: ts(c.min), by: 1,
        files: c.files, tests: c.tests, approval: null, chat: []
      };
      x.chat.push({ from: "owner", at: ts(c.min), text: c.ask, shot: c.shot || null });
      if (c.status === "beta" && !c.tests) {
        x.chat.push({ from: "claude", at: ts(c.min - 1), text: "Working on this on beta now. I will run the tests when the change is done." });
      } else {
        x.chat.push({ from: "claude", at: ts(c.min - 6), text: summaryFor(c) });
      }
      if (c.sentMin != null) {
        x.approval = { token: "demo" + c.id + "x" + hex(c.id), sentAt: ts(c.sentMin), checklist: ["Open the test site", "Try: " + c.title, "Check that nothing else looks different"], decision: null, comment: "", decidedAt: null };
        if (c.decidedMin != null) { x.approval.decision = "approved"; x.approval.decidedAt = ts(c.decidedMin); }
      }
      if (c.liveMin != null) x.liveAt = ts(c.liveMin);
      return x;
    });
    s.alerts = D.alerts.map(function (a) { return { id: s.seq++, projectId: a.projectId, text: a.text, at: ts(a.min), seen: false }; });
    s.log = D.log.map(function (l) { return { at: ts(l.min), userId: l.userId, projectId: l.projectId, action: l.action, details: l.details, prompt: l.prompt || "" }; });
    return s;
  }

  function summaryFor(c) {
    if (!c.tests) return "Done on beta.";
    var t = c.tests[0] === c.tests[1]
      ? "All " + c.tests[1] + " tests passed."
      : c.tests[0] + " of " + c.tests[1] + " tests passed. " + (c.tests[1] - c.tests[0]) + " failed — I'm fixing them on beta.";
    return "Done on beta: " + c.title.charAt(0).toLowerCase() + c.title.slice(1) + ". I changed " + c.files.length +
      (c.files.length === 1 ? " file." : " files.") + " " + t + (c.tests[0] === c.tests[1] ? " Please check it on beta, then send it to the client." : "");
  }

  var state = null;
  function S() {
    if (state) return state;
    var saved = get("apd.state");
    state = saved && saved.v === D.DATA_VERSION ? saved : build();
    if (!saved || saved.v !== D.DATA_VERSION) save();
    return state;
  }
  function save() { set("apd.state", state); }
  function resetState() { state = build(); save(); }
  function nextId() { var s = S(); s.seq += 1; return s.seq; }

  function project(id) { id = Number(id); return S().projects.filter(function (p) { return p.id === id; })[0] || null; }
  function user(id) { return S().users.filter(function (u) { return u.id === id; })[0] || null; }
  function userName(id) { if (!id) return "System"; var u = user(id); return u ? u.name : "Unknown"; }
  function isOwner(u) { return u && u.role === "owner"; }
  /* F02: a developer sees only assigned projects. */
  function canSee(u, projectId) {
    if (!u) return false;
    if (isOwner(u)) return true;
    var full = user(u.id);
    return !!(full && full.active && full.projects !== "all" && full.projects.indexOf(Number(projectId)) !== -1);
  }
  function myProjects(u) { return S().projects.filter(function (p) { return canSee(u, p.id); }); }
  function changesOf(pid) { return S().changes.filter(function (c) { return c.projectId === pid; }); }
  function lastDeploy(pid) {
    var d = S().deployments.filter(function (x) { return x.projectId === pid && x.env === "live"; });
    d.sort(function (a, b) { return b.at - a.at; });
    return d[0] || null;
  }
  function lastBackup(pid) {
    var b = S().backups.filter(function (x) { return x.projectId === pid && x.ok && x.type !== "snapshot"; });
    b.sort(function (a, b2) { return b2.at - a.at; });
    return b[0] || null;
  }
  function lastSnapshot(pid) {
    var b = S().backups.filter(function (x) { return x.projectId === pid && x.ok && x.type === "snapshot"; });
    b.sort(function (a, b2) { return b2.at - a.at; });
    return b[0] || null;
  }

  /* Health: red = live incident; yellow = stale backup, failing tests or a slow approval; else green. */
  function health(p) {
    if (p.incident) return { level: "bad", reason: p.incident };
    var lb = lastBackup(p.id);
    if (!lb || minSince(lb.at) > D.BACKUP_STALE_MIN) return { level: "warn", reason: "Last backup is " + (lb ? ago(lb.at).replace(" ago", "") : "missing") + " old" };
    var ch = changesOf(p.id);
    for (var i = 0; i < ch.length; i++) {
      if (ch[i].status === "beta" && ch[i].tests && ch[i].tests[0] < ch[i].tests[1]) return { level: "warn", reason: "Tests failing on beta" };
    }
    for (var j = 0; j < ch.length; j++) {
      var a = ch[j].approval;
      if (ch[j].status === "client" && a && minSince(a.sentAt) > D.APPROVAL_SLOW_MIN) return { level: "warn", reason: "Client approval waiting " + ago(a.sentAt).replace(" ago", "") };
    }
    return { level: "ok", reason: "" };
  }

  function log(action, projectId, details, prompt) {
    var u = currentUser();
    S().log.unshift({ at: Date.now(), userId: u ? u.id : 0, projectId: projectId || 0, action: action, details: details || "", prompt: prompt || "" });
    save();
  }
  function alert(projectId, text) {
    S().alerts.unshift({ id: nextId(), projectId: projectId, text: text, at: Date.now(), seen: false });
    save();
  }

  /* ---------- dialogs ---------- */
  /* opts: { title, body (html), wide, actions: [{ label, kind: "primary"|"danger"|"", id, onClick(dlg) -> false keeps it open }] }
     Dialogs with fields close only from ✕ / Cancel (no outside click / Esc), so typed text is never lost. */
  function dialog(opts) {
    var dlg = document.createElement("dialog");
    dlg.className = "dlg" + (opts.wide ? " dlg-wide" : "");
    var acts = (opts.actions || []).map(function (a, i) {
      return '<button type="button" class="btn ' + (a.kind === "primary" ? "btn-primary" : a.kind === "danger" ? "btn-danger" : "") + '" data-act="' + i + '"' + (a.id ? ' id="' + a.id + '"' : "") + '>' + a.label + '</button>';
    }).join("");
    dlg.innerHTML =
      '<div class="dlg-head"><h2>' + esc(opts.title) + '</h2><button type="button" class="icon-btn" data-close aria-label="Close">' + ICONS.x + '</button></div>' +
      '<div class="dlg-body">' + opts.body + '</div>' +
      '<div class="dlg-foot"><button type="button" class="btn" data-close>Cancel</button>' + acts + '</div>';
    document.body.appendChild(dlg);
    dlg.addEventListener("click", function (e) {
      if (e.target.closest("[data-close]")) { dlg.close(); return; }
      var b = e.target.closest("[data-act]");
      if (b && !b.disabled) {
        var a = opts.actions[Number(b.getAttribute("data-act"))];
        if (a.onClick && a.onClick(dlg) === false) return;
        dlg.close();
      }
    });
    dlg.addEventListener("cancel", function (e) { if (dlg.querySelector("input, textarea, select")) e.preventDefault(); });
    dlg.addEventListener("close", function () { setTimeout(function () { dlg.remove(); }, 0); });
    dlg.showModal();
    return dlg;
  }

  /* OTP block for Go Live / Restore / Rollback (demo code in data.js). */
  function otpHtml() {
    return '<div class="field"><label for="dlg-otp">OTP sent to your mobile</label>' +
      '<input id="dlg-otp" class="otp-small mono" inputmode="numeric" maxlength="6" autocomplete="one-time-code" placeholder="6-digit code"></div>' +
      '<p class="hint">Demo code: <span class="mono">' + D.OTP + '</span></p><div class="msg msg-bad" data-otp-err hidden></div>';
  }
  function otpOk(dlg) {
    var v = (dlg.querySelector("#dlg-otp").value || "").replace(/\D/g, "");
    var err = dlg.querySelector("[data-otp-err]");
    if (v === D.OTP) { err.hidden = true; return true; }
    err.textContent = v.length === 6 ? "That code is not right." : "Enter the 6-digit code.";
    err.hidden = false;
    return false;
  }

  /* Shows steps one by one inside el, then calls done(). */
  function runSteps(el, steps, done) {
    el.innerHTML = '<ol class="steps-run">' + steps.map(function (s) { return '<li>' + esc(s) + '</li>'; }).join("") + '</ol>';
    var items = el.querySelectorAll("li"), i = 0;
    function next() {
      if (i > 0) items[i - 1].className = "done";
      if (i >= items.length) { if (done) done(); return; }
      items[i].className = "doing";
      i += 1;
      setTimeout(next, 650);
    }
    next();
  }

  function copy(text, okMsg) {
    try {
      navigator.clipboard.writeText(text).then(function () { toast(okMsg || "Copied."); }, function () { toast("Copy failed — select the text and copy it."); });
    } catch (e) { toast("Copy failed — select the text and copy it."); }
  }

  window.APP = {
    ICONS: ICONS, get: get, set: set, toast: toast, ago: ago, agoMin: agoMin, minSince: minSince, stamp: stamp, esc: esc,
    slug: slug, hex: hex, hue: hue, techHue: techHue, initials: initials, token: token, qs: qs, copy: copy,
    currentUser: currentUser, signIn: signIn, signOut: signOut,
    S: S, save: save, resetState: resetState, nextId: nextId,
    project: project, user: user, userName: userName, isOwner: isOwner, canSee: canSee, myProjects: myProjects,
    changesOf: changesOf, lastDeploy: lastDeploy, lastBackup: lastBackup, lastSnapshot: lastSnapshot, health: health,
    log: log, alert: alert, dialog: dialog, otpHtml: otpHtml, otpOk: otpOk, runSteps: runSteps, paintTheme: paintThemeButtons
  };

  document.addEventListener("DOMContentLoaded", function () {
    paintThemeButtons();
    document.addEventListener("click", function (e) {
      var t = e.target.closest ? e.target.closest("button[data-theme-toggle]") : null;
      if (t) toggleTheme();
    });
  });
})();

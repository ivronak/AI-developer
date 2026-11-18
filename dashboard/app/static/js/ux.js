/* UX helpers (presentation only, no data written): Ctrl/Cmd+K command palette, toast close button, unsaved-changes bar, number count-up. */
(function () {
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  function el(tag, cls, text) { var n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  var ICON_SEARCH = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>';

  /* ---------- Command palette ---------- */
  var pal, input, list, items = [], shown = [], sel = 0, lastFocus = null, projectsLoaded = false, projects = [];

  function pageItems() {
    var out = [], i, a, b;
    var nav = document.querySelectorAll(".inavs a");
    for (i = 0; i < nav.length; i++) {
      a = nav[i]; out.push({ group: "Go to", label: a.getAttribute("aria-label") || a.textContent.trim(), hint: "Page", href: a.getAttribute("href") });
    }
    var tabs = document.querySelectorAll("[data-tab]");
    for (i = 0; i < tabs.length; i++) (function (t) {
      out.push({ group: "This project", label: t.textContent.trim() + " tab", hint: "Tab", run: function () { t.click(); var tb = document.getElementById("tabs"); if (tb) tb.scrollIntoView({ block: "start" }); } });
    })(tabs[i]);
    b = document.getElementById("bell");
    if (b) out.push({ group: "Actions", label: "Open alerts", hint: "Alerts", run: function () { b.click(); } });
    b = document.querySelector("[data-theme-toggle]");
    if (b) out.push({ group: "Actions", label: "Switch light / dark theme", hint: "Theme", run: function () { b.click(); } });
    return out;
  }

  function parseProjects(doc) {
    var out = [], seen = {}, links = doc.querySelectorAll('a[href^="/projects/"]');
    for (var i = 0; i < links.length; i++) {
      var a = links[i], href = a.getAttribute("href");
      if (!/^\/projects\/\d+$/.test(href) || seen[href]) continue;
      var n = a.querySelector(".pc-id strong, .nm strong"); if (!n) continue;
      var sub = a.querySelector(".pc-id span, .nm span");
      seen[href] = 1;
      out.push({ group: "Projects", label: n.textContent.trim(), hint: sub ? sub.textContent.trim() : "Project", href: href });
    }
    return out;
  }

  function loadProjects() {
    if (projectsLoaded) return;
    projectsLoaded = true;
    projects = parseProjects(document);
    if (projects.length) { rebuild(); return; }
    if (!window.fetch || !window.DOMParser) return;
    fetch("/projects", { credentials: "same-origin" }).then(function (r) { return r.ok ? r.text() : ""; }).then(function (t) {
      if (t) projects = parseProjects(new DOMParser().parseFromString(t, "text/html"));
      rebuild();
    }).catch(function () { rebuild(); });
  }

  function score(it, q) {
    var l = it.label.toLowerCase(), h = (it.hint || "").toLowerCase();
    if (!q) return 1;
    var parts = q.split(/\s+/), s = 0;
    for (var i = 0; i < parts.length; i++) {
      var p = parts[i]; if (!p) continue;
      if (l.indexOf(p) === 0) s += 3; else if (l.indexOf(p) > -1) s += 2; else if (h.indexOf(p) > -1) s += 1; else return 0;
    }
    return s;
  }

  function render() {
    var q = input.value.trim().toLowerCase(), scored = [], i;
    for (i = 0; i < items.length; i++) { var s = score(items[i], q); if (s) scored.push({ it: items[i], s: s, i: i }); }
    if (q) scored.sort(function (a, b) { return b.s - a.s || a.i - b.i; });
    shown = scored.slice(0, 40).map(function (x) { return x.it; });
    if (sel >= shown.length) sel = Math.max(0, shown.length - 1);
    list.textContent = "";
    if (!shown.length) {
      list.appendChild(el("li", "cmdk-empty", projectsLoaded ? "Nothing matches “" + input.value.trim() + "”" : "Loading…"));
      return;
    }
    var lastGroup = "";
    for (i = 0; i < shown.length; i++) (function (it, idx) {
      if (it.group !== lastGroup) { lastGroup = it.group; var g = el("li", "cmdk-group", it.group); g.setAttribute("role", "presentation"); list.appendChild(g); }
      var li = el("li", "cmdk-item" + (idx === sel ? " is-sel" : ""));
      li.setAttribute("role", "option"); li.setAttribute("aria-selected", idx === sel ? "true" : "false"); li.id = "cmdk-o" + idx;
      li.appendChild(el("span", "cmdk-l", it.label)); li.appendChild(el("span", "cmdk-h", it.hint || ""));
      li.addEventListener("mousemove", function () { if (sel !== idx) { sel = idx; paintSel(false); } });
      li.addEventListener("click", function () { sel = idx; choose(); });
      list.appendChild(li);
    })(shown[i], i);
    input.setAttribute("aria-activedescendant", "cmdk-o" + sel);
  }
  function paintSel(scroll) {
    var all = list.querySelectorAll(".cmdk-item");
    for (var i = 0; i < all.length; i++) {
      var on = i === sel; all[i].classList.toggle("is-sel", on); all[i].setAttribute("aria-selected", on ? "true" : "false");
      if (on && scroll) all[i].scrollIntoView({ block: "nearest" });
    }
    input.setAttribute("aria-activedescendant", "cmdk-o" + sel);
  }
  function rebuild() { items = pageItems().concat(projects); if (pal && !pal.hidden) render(); }
  function choose() {
    var it = shown[sel]; if (!it) return;
    closePalette(true);
    if (it.href) window.location.href = it.href; else if (it.run) it.run();
  }

  function build() {
    pal = el("div", "cmdk"); pal.id = "cmdk"; pal.hidden = true;
    pal.setAttribute("role", "dialog"); pal.setAttribute("aria-modal", "true"); pal.setAttribute("aria-label", "Search and jump");
    var box = el("div", "cmdk-box"), top = el("div", "cmdk-in");
    top.innerHTML = ICON_SEARCH;
    input = el("input"); input.id = "cmdk-q"; input.type = "text"; input.placeholder = "Search projects, pages, actions…";
    input.autocomplete = "off"; input.spellcheck = false; input.setAttribute("role", "combobox"); input.setAttribute("aria-controls", "cmdk-list"); input.setAttribute("aria-expanded", "true");
    top.appendChild(input); top.appendChild(el("kbd", "", "esc"));
    list = el("ul", "cmdk-list"); list.id = "cmdk-list"; list.setAttribute("role", "listbox");
    var foot = el("div", "cmdk-foot");
    foot.innerHTML = "<span><kbd>↑</kbd><kbd>↓</kbd> move</span><span><kbd>↵</kbd> open</span><span><kbd>esc</kbd> close</span>";
    box.appendChild(top); box.appendChild(list); box.appendChild(foot); pal.appendChild(box); document.body.appendChild(pal);
    pal.addEventListener("mousedown", function (e) { if (e.target === pal) closePalette(); });
    input.addEventListener("input", function () { sel = 0; render(); });
    input.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown") { e.preventDefault(); if (shown.length) { sel = (sel + 1) % shown.length; paintSel(true); } }
      else if (e.key === "ArrowUp") { e.preventDefault(); if (shown.length) { sel = (sel - 1 + shown.length) % shown.length; paintSel(true); } }
      else if (e.key === "Enter") { e.preventDefault(); choose(); }
      else if (e.key === "Escape") { e.preventDefault(); closePalette(); }
      else if (e.key === "Tab") { e.preventDefault(); }
    });
  }
  function openPalette() {
    if (!pal) build();
    if (document.querySelector("dialog[open]")) return;
    lastFocus = document.activeElement;
    items = pageItems().concat(projects); sel = 0; input.value = "";
    pal.hidden = false; document.documentElement.classList.add("cmdk-open");
    render(); input.focus(); loadProjects();
  }
  function closePalette(skipFocus) {
    if (!pal || pal.hidden) return;
    pal.hidden = true; document.documentElement.classList.remove("cmdk-open");
    if (!skipFocus && lastFocus && lastFocus.focus) try { lastFocus.focus(); } catch (e) {}
  }

  document.addEventListener("keydown", function (e) {
    var tag = (e.target.tagName || "").toLowerCase();
    if ((e.key === "k" || e.key === "K") && (e.metaKey || e.ctrlKey) && !e.altKey) {
      e.preventDefault(); if (pal && !pal.hidden) closePalette(); else openPalette(); return;
    }
    if (e.key === "/" && !e.metaKey && !e.ctrlKey && !e.altKey && tag !== "input" && tag !== "textarea" && tag !== "select" && !e.target.isContentEditable) {
      if (!document.querySelector("dialog[open]")) { e.preventDefault(); openPalette(); }
    }
  });
  /* ---------- chat dock: the project chat sits beside the work (right / left), resizable, can be hidden ---------- */
  function chatDock() {
    var panel = document.querySelector(".chat-panel"); if (!panel || !window.matchMedia) return;
    var root = document.documentElement, mq = window.matchMedia("(min-width: 1100px)");
    function get(k, d) { try { var v = localStorage.getItem(k); return v === null ? d : v; } catch (e) { return d; } }
    function put(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }
    var mode = get("apd.dock", "right"), storedW = get("apd.dockw", null);
    function defW() { return Math.max(360, Math.min(520, Math.round(window.innerWidth * 0.36))); }
    var w = storedW ? (parseInt(storedW, 10) || defW()) : defW();
    var ICON = {
      side: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16"/></svg>',
      wide: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/></svg>',
      hide: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6L6 18M6 6l12 12"/></svg>'
    };
    var view = get("apd.dockview", "normal");
    var h1 = document.querySelector(".phead-id h1");
    var bar = el("div", "dock-bar"), title = el("strong", "", "Chat"); bar.appendChild(title);
    var sub = el("span", "", h1 && h1.firstChild ? h1.firstChild.textContent.trim() : ""); sub.style.cssText = "font-size:12.5px;color:var(--muted);margin-right:auto;max-width:130px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap";
    title.style.marginRight = "6px"; bar.appendChild(sub);
    function btn(act, label) { var b = el("button"); b.type = "button"; b.setAttribute("data-dock-act", act); b.setAttribute("aria-label", label); b.title = label; b.innerHTML = ICON[act]; bar.appendChild(b); return b; }
    btn("side", "Dock to the other side"); btn("wide", "Wider / narrower"); btn("hide", "Hide the dock (back to the Chat tab)");
    panel.insertBefore(bar, panel.firstChild);
    var handle = el("div", "dock-resize"); handle.setAttribute("aria-hidden", "true"); panel.appendChild(handle);
    var fab = el("button", "dock-fab", "Dock the chat"); fab.type = "button"; fab.hidden = true; document.body.appendChild(fab);
    function apply() {
      var on = mq.matches && mode !== "off";
      root.classList.toggle("chat-docked", on); root.classList.toggle("dock-left", on && mode === "left");
      root.style.setProperty("--dock-w", (view === "wide" ? Math.max(w, 620) : w) + "px"); fab.hidden = !(mq.matches && mode === "off");
      root.classList.toggle("chat-focus", on && view === "focus");
      var ab = bar.querySelector('[data-dock-act="wide"]'); if (ab) { var tt = view === "normal" ? "Make the chat wider" : view === "wide" ? "Focus: chat only" : "Back to the side dock (Esc)"; ab.title = tt; ab.setAttribute("aria-label", tt); }
      var ob2 = document.querySelectorAll(".ccard-acts a.btn"); for (var oi = 0; oi < ob2.length; oi++) if (!ob2[oi].title) ob2[oi].title = ob2[oi].textContent.trim() || "Open";
      var pref = get("apd.side", "0") === "1";
      root.classList.toggle("side-collapsed", (on && window.innerWidth < 1500) || pref);
      var comp = document.getElementById("composer");
      if (comp && !comp.querySelector(".opts-tgl")) {
        var plus = comp.querySelector(".plus-btn");
        if (plus && plus.parentNode) {
          var ob = el("button", "opts-tgl"); ob.type = "button"; ob.title = "Engine, account, model and effort"; ob.setAttribute("aria-label", "Chat options");
          ob.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h10M18 7h2M4 17h2M10 17h10"/><circle cx="16" cy="7" r="2"/><circle cx="8" cy="17" r="2"/></svg>';
          ob.addEventListener("click", function () { comp.classList.toggle("opts-open"); });
          plus.parentNode.insertBefore(ob, plus.nextSibling);
        }
      }
      var heads = document.querySelectorAll(".ccard-head");
      for (var hi = 0; hi < heads.length; hi++) if (!heads[hi].querySelector(".ccard-tgl")) {
        var tg = el("button", "ccard-tgl"); tg.type = "button"; tg.setAttribute("aria-label", "Show or hide the change details"); tg.title = "Details";
        tg.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>';
        heads[hi].appendChild(tg);
      }
      if (on) { var cur = document.querySelector('[data-tab][aria-selected="true"]'); if (cur && cur.getAttribute("data-tab") === "chat") { var t = document.querySelector('[data-tab="changes"]'); if (t) t.click(); } }
    }
    bar.addEventListener("click", function (e) {
      var b = e.target.closest("[data-dock-act]"); if (!b) return; var act = b.getAttribute("data-dock-act");
      if (act === "side") { mode = mode === "left" ? "right" : "left"; put("apd.dock", mode); }
      else if (act === "wide") { view = view === "normal" ? "wide" : view === "wide" ? "focus" : "normal"; put("apd.dockview", view); }
      else if (act === "hide") { mode = "off"; put("apd.dock", mode); var c = document.querySelector('[data-tab="chat"]'); }
      apply();
      if (mode === "off") { var c2 = document.querySelector('[data-tab="chat"]'); if (c2) c2.click(); }
    });
    fab.addEventListener("click", function () { mode = "right"; put("apd.dock", mode); apply(); });
    handle.addEventListener("pointerdown", function (e) {
      e.preventDefault(); root.classList.add("dock-dragging"); handle.setPointerCapture(e.pointerId);
      function move(ev) {
        var left = root.classList.contains("dock-left");
        var nw = left ? ev.clientX - (parseInt(getComputedStyle(root).getPropertyValue("--side-w"), 10) || 240) - 8 : window.innerWidth - ev.clientX - 8;
        w = Math.max(320, Math.min(760, Math.round(nw))); root.style.setProperty("--dock-w", w + "px");
      }
      function up() { root.classList.remove("dock-dragging"); handle.removeEventListener("pointermove", move); handle.removeEventListener("pointerup", up); put("apd.dockw", String(w)); }
      handle.addEventListener("pointermove", move); handle.addEventListener("pointerup", up);
    });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape" && view === "focus" && !document.querySelector("dialog[open]") && !(pal && !pal.hidden)) { view = "normal"; put("apd.dockview", view); apply(); } });
    if (mq.addEventListener) mq.addEventListener("change", apply); else if (mq.addListener) mq.addListener(apply);
    document.addEventListener("click", function (e) { var t = e.target.closest && e.target.closest(".ccard-tgl"); if (t) { var c = t.closest(".ccard"); if (c) c.classList.toggle("is-open"); } });
    apply(); window.addEventListener("load", apply); window.addEventListener("resize", apply);
  }

  /* rails stay in view while the page scrolls (a tall rail sticks by its bottom edge, a short one by its top) */
  function stickyRails() {
    var rails = document.querySelectorAll(".p-rail, .ov-side"); if (!rails.length) return;
    function fit() { for (var i = 0; i < rails.length; i++) { var h = rails[i].offsetHeight; rails[i].style.top = Math.min(16, window.innerHeight - h - 16) + "px"; } }
    fit(); window.addEventListener("resize", fit);
    if (window.ResizeObserver) for (var k = 0; k < rails.length; k++) new ResizeObserver(fit).observe(rails[k]);
  }
  /* project page: a change in the rail opens in the chat */
  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("[data-goto-change]"); if (!b) return;
    var tab = document.querySelector('[data-tab="chat"]'); if (tab && !document.documentElement.classList.contains("chat-docked")) tab.click();
    var pk = document.querySelector('.pick [data-pick="' + b.getAttribute("data-goto-change") + '"]'); if (pk) pk.click();
    var tb = document.getElementById("tabs"); if (tb && !document.documentElement.classList.contains("chat-docked")) tb.scrollIntoView({ block: "start" });
  });

  /* sidebar: your projects (needs attention first, then recent), with status dots */
  function sidebarProjects() {
    var nav = document.querySelector(".topbar-in"), acts = document.querySelector(".top-actions"); if (!nav || !acts) return;
    var box = el("nav", "sb-projects"); box.setAttribute("aria-label", "Projects"); nav.insertBefore(box, acts);
    var here = (location.pathname.match(/^\/projects\/(\d+)/) || [])[1], cur = null;
    if (here) { var h1 = document.querySelector(".phead-id h1, .phead h1"); if (h1) { cur = { href: "/projects/" + here, name: h1.firstChild ? h1.firstChild.textContent.trim() : h1.textContent.trim() }; remember(cur); } }
    function remember(p) {
      try { var r = JSON.parse(localStorage.getItem("apd.recent") || "[]").filter(function (x) { return x.href !== p.href; }); r.unshift({ href: p.href, name: p.name }); localStorage.setItem("apd.recent", JSON.stringify(r.slice(0, 8))); } catch (e) {}
    }
    function paint(list) {
      box.textContent = "";
      var recent = []; try { recent = JSON.parse(localStorage.getItem("apd.recent") || "[]"); } catch (e) {}
      var byHref = {}, i; for (i = 0; i < list.length; i++) byHref[list[i].href] = list[i];
      var attn = list.filter(function (p) { return p.level === "bad" || p.level === "warn"; }).slice(0, 6);
      function group(title, items) {
        if (!items.length) return;
        box.appendChild(el("span", "nav-label", title));
        items.forEach(function (p) {
          var a = el("a", "sb-p" + (location.pathname === p.href ? " is-on" : "")); a.href = p.href; a.title = p.name;
          var d = el("span", "sb-dot lv-" + (p.level || "ok")); a.appendChild(d); a.appendChild(el("span", "sb-n", p.name)); box.appendChild(a);
        });
      }
      group("Needs attention", attn);
      var shown = {}; attn.forEach(function (p) { shown[p.href] = 1; });
      var rec = recent.filter(function (r) { return !shown[r.href]; }).slice(0, 5).map(function (r) { return { href: r.href, name: byHref[r.href] ? byHref[r.href].name : r.name, level: byHref[r.href] ? byHref[r.href].level : "ok" }; });
      group("Recent", rec);
    }
    function load() {
      var cached = null; try { cached = JSON.parse(sessionStorage.getItem("apd.sbp") || "null"); } catch (e) {}
      if (cached && Date.now() - cached.t < 180000) { paint(cached.list); return; }
      if (document.querySelector(".pcard[data-level]")) { var l = []; document.querySelectorAll(".pcard[data-level]").forEach(function (a) { var n = a.querySelector(".pc-id strong"); l.push({ href: a.getAttribute("href"), name: n ? n.textContent.trim() : "", level: a.getAttribute("data-level") }); }); store(l); paint(l); return; }
      if (!window.fetch || !window.DOMParser) return;
      fetch("/projects", { credentials: "same-origin" }).then(function (r) { return r.ok ? r.text() : ""; }).then(function (t) {
        if (!t) return; var d = new DOMParser().parseFromString(t, "text/html"), l = [];
        d.querySelectorAll(".pcard[data-level]").forEach(function (a) { var n = a.querySelector(".pc-id strong"); l.push({ href: a.getAttribute("href"), name: n ? n.textContent.trim() : "", level: a.getAttribute("data-level") }); });
        store(l); paint(l);
      }).catch(function () {});
    }
    function store(l) { try { sessionStorage.setItem("apd.sbp", JSON.stringify({ t: Date.now(), list: l })); } catch (e) {} }
    load();
  }

  /* projects page: the view (cards / list) is remembered */
  function rememberView() {
    var tg = document.querySelector(".view-toggle"); if (!tg) return;
    var want = null; try { want = localStorage.getItem("apd.view"); } catch (e) {}
    if (!want) want = "list";
    if (want) { var b = tg.querySelector('[data-view="' + want + '"]'); if (b && b.getAttribute("aria-pressed") !== "true") b.click(); }
    tg.addEventListener("click", function (e) { var b = e.target.closest("[data-view]"); if (b) try { localStorage.setItem("apd.view", b.getAttribute("data-view")); } catch (err) {} });
  }

  /* sidebar: collapse to icons, remembered */
  document.addEventListener("click", function (e) {
    var t = e.target.closest && e.target.closest("[data-side-toggle]"); if (!t) return;
    var on = document.documentElement.classList.toggle("side-collapsed");
    try { localStorage.setItem("apd.side", on ? "1" : "0"); } catch (err) {}
    t.setAttribute("aria-label", on ? "Expand the sidebar" : "Collapse the sidebar"); t.setAttribute("data-tip", on ? "Expand" : "Collapse");
  });

  /* activity log: quick find, long details fold */
  function initLog() {
    var rows = document.querySelector(".log-sec .rows"); if (!rows) return;
    var inp = el("input", "log-find"); inp.type = "search"; inp.placeholder = "Find in this list — a person, project or word…"; inp.setAttribute("aria-label", "Find in the activity list");
    rows.parentNode.insertBefore(inp, rows);
    inp.addEventListener("input", function () {
      var q = inp.value.trim().toLowerCase(), kids = rows.children, vis = false, head = null, anyInGroup = false, i;
      function flush() { if (head) head.hidden = !anyInGroup; }
      for (i = 0; i < kids.length; i++) {
        var k = kids[i];
        if (k.classList.contains("day-head")) { flush(); head = k; anyInGroup = false; continue; }
        var hit = !q || k.textContent.toLowerCase().indexOf(q) > -1; k.hidden = !hit; if (hit) anyInGroup = true;
      }
      flush();
    });
    rows.addEventListener("click", function (e) {
      var t = e.target.closest(".row-main .row-sub, .log-prompt"); if (t) t.classList.toggle("is-open");
    });
  }

  document.addEventListener("click", function (e) {
    if (e.target.closest("[data-cmdk]")) { e.preventDefault(); openPalette(); return; }
    if (e.target.closest("#find-btn") && !document.querySelector("[data-find]")) openPalette();
  });

  /* ---------- Toasts: a close button on each ---------- */
  function decorateToast(t) {
    if (t.getAttribute("data-x")) return; t.setAttribute("data-x", "1");
    var x = el("button", "toast-x", "×"); x.type = "button"; x.setAttribute("aria-label", "Dismiss");
    x.addEventListener("click", function () { t.remove(); });
    t.appendChild(x);
  }

  /* ---------- Unsaved-changes bar (settings forms) ---------- */
  var bar = null, curForm = null;
  function trackable(f) {
    if (!f || (f.method || "").toLowerCase() !== "post" || f.closest("dialog") || f.classList.contains("composer")) return false;
    return !!f.closest(".card, .panel-box");
  }
  function showBar(f) {
    curForm = f;
    if (!bar) {
      bar = el("div", "unsaved"); bar.setAttribute("role", "status");
      bar.appendChild(el("span", "unsaved-t", "Unsaved changes"));
      var r = el("button", "btn btn-sm", "Reset"); r.type = "button";
      r.addEventListener("click", function () { if (curForm) curForm.reset(); hideBar(); });
      var s = el("button", "btn btn-sm btn-primary", "Save"); s.type = "button";
      s.addEventListener("click", function () { if (curForm && curForm.requestSubmit) curForm.requestSubmit(); else if (curForm) curForm.submit(); hideBar(); });
      bar.appendChild(r); bar.appendChild(s); document.body.appendChild(bar);
    }
    bar.classList.add("is-on");
  }
  function hideBar() { if (bar) bar.classList.remove("is-on"); curForm = null; }
  document.addEventListener("input", function (e) {
    var f = e.target.closest && e.target.closest("form");
    if (!trackable(f) || e.target.type === "hidden" || e.target.hasAttribute("data-no-dirty")) return;
    showBar(f);
  });
  document.addEventListener("change", function (e) {
    var f = e.target.closest && e.target.closest("form");
    if (!trackable(f) || e.target.type === "hidden" || e.target.hasAttribute("data-no-dirty")) return;
    showBar(f);
  });
  document.addEventListener("submit", hideBar);
  window.addEventListener("pageshow", hideBar);

  /* ---------- Numbers count up once ---------- */
  function countUp(n) {
    var end = parseInt(n.textContent, 10);
    if (!/^\d+$/.test(n.textContent.trim()) || end < 2 || end > 99999) return;
    var t0 = null, dur = 650;
    function step(ts) {
      if (t0 === null) t0 = ts;
      var p = Math.min(1, (ts - t0) / dur), e = 1 - Math.pow(1 - p, 3);
      n.textContent = String(Math.round(end * e));
      if (p < 1) requestAnimationFrame(step); else n.textContent = String(end);
    }
    n.textContent = "0"; requestAnimationFrame(step);
  }

  /* in-page jump bar: the link of the section on screen is highlighted */
  function spy() {
    var links = document.querySelectorAll(".jump a"); if (!links.length || !window.IntersectionObserver) return;
    var map = {}, i;
    for (i = 0; i < links.length; i++) { var id = (links[i].getAttribute("href") || "").slice(1); var t = id && document.getElementById(id); if (t) map[id] = { a: links[i], t: t }; }
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (e) {
        if (!e.isIntersecting) return;
        for (var k in map) map[k].a.classList.toggle("is-on", map[k].t === e.target);
      });
    }, { rootMargin: "-20% 0px -65% 0px" });
    for (var k in map) io.observe(map[k].t);
  }

  /* settings: rows open on demand, the open ones are remembered, long intro text folds to two lines */
  function clampIn(d) {
    var ps = d.querySelectorAll(".set-body > p.hint, .set-pane > p.hint");
    for (var i = 0; i < ps.length; i++) (function (p) {
      if (p.getAttribute("data-c") || p.offsetParent === null) return; p.setAttribute("data-c", "1"); p.classList.add("is-clamped");
      if (p.scrollHeight <= p.clientHeight + 2) { p.classList.remove("is-clamped"); return; }
      var b = el("button", "more-btn", "Show more"); b.type = "button";
      b.addEventListener("click", function () { var c = p.classList.toggle("is-clamped"); b.textContent = c ? "Show more" : "Show less"; });
      p.parentNode.insertBefore(b, p.nextSibling);
    })(ps[i]);
  }
  /* a long section becomes tabs (one per sub-heading) so nothing needs a long scroll */
  function tabsFor(d) {
    var body = d.querySelector(".set-body"); if (!body || body.getAttribute("data-tabs")) return;
    var kids = Array.prototype.slice.call(body.children), heads = 0, i;
    for (i = 0; i < kids.length; i++) if (kids[i].tagName === "H3") heads++;
    if (heads < 2) return;
    body.setAttribute("data-tabs", "1");
    var groups = [], cur = { title: "About", els: [], def: false, about: true };
    for (i = 0; i < kids.length; i++) {
      if (kids[i].tagName === "H3") { groups.push(cur); cur = { title: kids[i].textContent.trim(), els: [], def: kids[i].hasAttribute("data-default") }; kids[i].remove(); }
      else cur.els.push(kids[i]);
    }
    groups.push(cur);
    var useful = groups.filter(function (g) { return g.els.length; }), about = null, panes = [];
    for (i = 0; i < useful.length; i++) { if (useful[i].about) about = useful[i]; else panes.push(useful[i]); }
    if (about) panes.push(about);
    if (panes.length < 2) return;
    var bar = el("div", "set-tabs"); bar.setAttribute("role", "tablist");
    var key = "set-tab-" + d.id, want = null, first = about ? 0 : 0, def = 0;
    try { want = sessionStorage.getItem(key); } catch (e) {}
    for (i = 0; i < panes.length; i++) if (panes[i].def) def = i;
    var idx = def;
    for (i = 0; i < panes.length; i++) if (want && panes[i].title === want) idx = i;
    var btns = [], paneEls = [];
    function show(n) {
      for (var k = 0; k < panes.length; k++) { var on = k === n; btns[k].setAttribute("aria-selected", on ? "true" : "false"); paneEls[k].hidden = !on; }
      try { sessionStorage.setItem(key, panes[n].title); } catch (e) {}
      clampIn(d);
    }
    for (i = 0; i < panes.length; i++) (function (n) {
      var b = el("button", "", panes[n].title); b.type = "button"; b.setAttribute("role", "tab");
      b.addEventListener("click", function () { show(n); });
      btns.push(b); bar.appendChild(b);
      var pe = el("div", "set-pane"); pe.setAttribute("role", "tabpanel");
      for (var m = 0; m < panes[n].els.length; m++) pe.appendChild(panes[n].els[m]);
      paneEls.push(pe);
    })(i);
    body.insertBefore(bar, body.firstChild);
    for (i = 0; i < paneEls.length; i++) body.appendChild(paneEls[i]);
    show(idx);
  }
  /* long lists get a filter box */
  function filterLists(d) {
    var lists = d.querySelectorAll(".rows");
    for (var i = 0; i < lists.length; i++) (function (list) {
      if (list.closest(".log-sec")) return;
      var rows = list.querySelectorAll(":scope > a.row, :scope > .row"); if (rows.length < 9 || list.getAttribute("data-f")) return;
      list.setAttribute("data-f", "1");
      var inp = el("input", "set-filter"); inp.type = "search"; inp.placeholder = "Filter " + rows.length + " items…"; inp.setAttribute("aria-label", "Filter the list");
      inp.addEventListener("input", function () {
        var q = inp.value.trim().toLowerCase();
        for (var r = 0; r < rows.length; r++) rows[r].hidden = !!q && rows[r].textContent.toLowerCase().indexOf(q) === -1;
      });
      list.parentNode.insertBefore(inp, list);
    })(lists[i]);
  }
  /* a one-click copy button under read-only keys */
  function copyButtons(d) {
    var tas = d.querySelectorAll("textarea[readonly]");
    for (var i = 0; i < tas.length; i++) (function (ta) {
      if (ta.getAttribute("data-copy")) return; ta.setAttribute("data-copy", "1");
      var b = el("button", "copy-key", "Copy"); b.type = "button";
      b.addEventListener("click", function () {
        function done() { b.textContent = "Copied ✓"; b.classList.add("is-done"); setTimeout(function () { b.textContent = "Copy"; b.classList.remove("is-done"); }, 1800); }
        if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(ta.value).then(done);
        else { ta.select(); try { document.execCommand("copy"); done(); } catch (e) {} }
      });
      ta.parentNode.insertBefore(b, ta.nextSibling);
    })(tas[i]);
  }
  function initSettings() {
    var sets = document.querySelectorAll("details.set"); if (!sets.length) return;
    var saved = [], i;
    try { saved = JSON.parse(sessionStorage.getItem("set-open") || "[]"); } catch (e) {}
    function store() { var ids = []; for (var k = 0; k < sets.length; k++) if (sets[k].open) ids.push(sets[k].id); try { sessionStorage.setItem("set-open", JSON.stringify(ids)); } catch (e) {} }
    for (i = 0; i < sets.length; i++) (function (d) {
      if (saved.indexOf(d.id) > -1) d.open = true;
      tabsFor(d); filterLists(d); copyButtons(d);
      d.addEventListener("toggle", function () { store(); if (d.open) clampIn(d); });
      if (d.open) clampIn(d);
    })(sets[i]);
    function openHash() {
      var t = location.hash && document.getElementById(location.hash.slice(1));
      if (t && t.tagName === "DETAILS") { t.open = true; setTimeout(function () { t.scrollIntoView({ block: "start" }); }, 60); }
    }
    document.addEventListener("click", function (e) {
      var a = e.target.closest && e.target.closest(".jump a"); if (!a) return;
      var t = document.getElementById((a.getAttribute("href") || "").slice(1)); if (t && t.tagName === "DETAILS") t.open = true;
    });
    openHash(); window.addEventListener("hashchange", openHash);
    var notes = document.querySelectorAll(".page > .pc-now.wrap:not(.chip-note), .note-row > .pc-now.wrap:not(.chip-note)");
    for (i = 0; i < notes.length; i++) (function (n) {
      if (!n.querySelector("small")) return; n.setAttribute("data-more", "1");
      n.addEventListener("click", function (e) { if (e.target.closest("a")) return; n.classList.toggle("is-open"); });
    })(notes[i]);
  }

  document.addEventListener("DOMContentLoaded", function () {
    spy(); initSettings(); initLog(); sidebarProjects(); rememberView(); chatDock(); stickyRails(); copyButtons(document); filterLists(document);
    var cn = document.querySelectorAll(".tabs-sm .n"); for (var c = 0; c < cn.length; c++) if (cn[c].textContent.trim() !== "0") cn[c].classList.add("nz");
    var kz = document.querySelectorAll(".kpi b");
    for (var z = 0; z < kz.length; z++) if (kz[z].textContent.trim() === "0") { var kp = kz[z].closest(".kpi"); if (kp) kp.classList.add("is-zero"); }
    var ts = document.querySelectorAll("#toasts .toast"), i;
    for (i = 0; i < ts.length; i++) decorateToast(ts[i]);
    var box = document.getElementById("toasts");
    if (box && window.MutationObserver) new MutationObserver(function (ms) {
      for (var a = 0; a < ms.length; a++) for (var b = 0; b < ms[a].addedNodes.length; b++) {
        var nd = ms[a].addedNodes[b]; if (nd.classList && nd.classList.contains("toast")) decorateToast(nd);
      }
    }).observe(box, { childList: true });
    if (!reduce) { var ks = document.querySelectorAll(".kpi b"); for (i = 0; i < ks.length; i++) countUp(ks[i]); }
  });
})();

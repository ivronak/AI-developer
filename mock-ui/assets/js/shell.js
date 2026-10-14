/* App shell for signed-in pages: sign-in guard, one top bar (breadcrumb + icon navigation + actions), phone tab bar, alerts panel.
   Page sets <body data-page="projects|backups|users"> (and data-owner-only for Owner pages).
   Breadcrumb: <body data-crumb="Overview">; a page can change it later with SHELL.setCrumb(text). */
(function () {
  var A = window.APP;
  var user = A.currentUser();
  if (!user) { window.location.replace("login.html"); return; }
  var full = A.user(user.id);
  if (!full || !full.active) { A.signOut(); return; }

  window.SHELL = { user: user, owner: A.isOwner(user), ready: false };

  document.addEventListener("DOMContentLoaded", function () {
    var page = document.body.getAttribute("data-page") || "";
    var owner = A.isOwner(user);
    var I = A.ICONS;
    var MARK = '<span class="brand-mark" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M5 7h14M5 12h10M5 17h6"/></svg></span>';
    var SECTION = { projects: ["All Projects", "projects.html"], backups: ["Backups", "backups.html"], users: ["Users & log", "users.html"] };
    var initials = user.name.split(" ").map(function (w) { return w.charAt(0); }).join("").slice(0, 2).toUpperCase();

    /* Icon navigation: active item shows icon + label, others icon only (label on hover / for screen readers). */
    function iconNav(key, href, icon, label, ownerOnly) {
      if (ownerOnly && !owner) return "";
      var on = page === key;
      return '<a href="' + href + '" class="inav' + (on ? " is-on" : "") + '"' + (on ? ' aria-current="page"' : "") +
        ' aria-label="' + label + '" data-tip="' + label + '">' + I[icon] + '<span class="inav-l">' + label + '</span></a>';
    }
    function tabItem(key, href, icon, label, ownerOnly) {
      if (ownerOnly && !owner) return "";
      return '<a href="' + href + '"' + (page === key ? ' class="is-on" aria-current="page"' : "") + '>' + I[icon] + '<span>' + label + '</span></a>';
    }

    var sec = SECTION[page] || SECTION.projects;
    var crumb = document.body.getAttribute("data-crumb") || "";
    var top = document.createElement("header");
    top.className = "topbar";
    top.innerHTML =
      '<div class="topbar-in">' +
        '<a class="brand" href="projects.html" aria-label="AI Project Dashboard — home">' + MARK + '</a>' +
        '<nav class="crumbs" aria-label="Breadcrumb"><a href="' + sec[1] + '">' + A.esc(sec[0]) + '</a>' +
          '<span class="sep" id="crumb-sep"' + (crumb ? "" : " hidden") + '>/</span><strong id="crumb">' + A.esc(crumb) + '</strong></nav>' +
        '<nav class="inavs" aria-label="Main">' +
          iconNav("projects", "projects.html", "grid", "Projects") +
          iconNav("backups", "backups.html", "db", "Backups") +
          iconNav("users", "users.html", "users", "Users & log", true) +
        '</nav>' +
        '<div class="top-actions">' +
          '<button class="icon-btn find-btn" type="button" id="find-btn" aria-label="Find (F)" data-tip="Find · F">' + I.search + '</button>' +
          '<button class="icon-btn" type="button" id="bell" aria-label="Alerts" aria-expanded="false" aria-controls="alerts-panel" data-tip="Alerts">' + I.bell + '</button>' +
          '<button class="icon-btn" type="button" data-theme-toggle aria-label="Switch light / dark theme" data-tip="Theme"></button>' +
          '<span class="me" title="' + A.esc(user.name) + ' · ' + (owner ? "Owner" : "Developer") + '"><span class="avatar">' + A.esc(initials) + '</span>' +
            '<span class="me-txt"><strong>' + A.esc(user.name) + '</strong><span>' + (owner ? "Owner" : "Developer") + '</span></span></span>' +
          '<button class="icon-btn" type="button" data-signout aria-label="Sign out" data-tip="Sign out">' + I.logout + '</button>' +
        '</div>' +
      '</div>';
    document.body.insertBefore(top, document.body.firstChild);

    var panel = document.createElement("section");
    panel.className = "alerts-panel"; panel.id = "alerts-panel"; panel.hidden = true;
    panel.setAttribute("aria-label", "Alerts");
    panel.innerHTML = '<div class="alerts-head"><strong>Alerts</strong><button class="link-btn" type="button" id="alerts-seen">Mark all seen</button></div><ul class="alerts-list" id="alerts-list"></ul>';
    top.insertAdjacentElement("afterend", panel);

    var tab = document.createElement("nav");
    tab.className = "tabbar"; tab.setAttribute("aria-label", "Main");
    tab.innerHTML =
      tabItem("projects", "projects.html", "grid", "Projects") +
      '<button type="button" id="tab-alerts">' + I.bell + '<span>Alerts</span></button>' +
      tabItem("backups", "backups.html", "db", "Backups") +
      tabItem("users", "users.html", "users", "Users", true);
    document.body.appendChild(tab);

    if (!document.getElementById("toasts")) {
      var t = document.createElement("div");
      t.className = "toasts"; t.id = "toasts"; t.setAttribute("aria-live", "polite");
      document.body.appendChild(t);
    }

    /* Alerts (F14) — only for projects this person can see */
    var bells = ["bell", "tab-alerts"];
    function mine() { return A.S().alerts.filter(function (a) { return A.canSee(user, a.projectId); }); }
    function renderAlerts() {
      var list = mine(), unseen = list.filter(function (a) { return !a.seen; }).length;
      var old = document.querySelectorAll(".badge");
      for (var i = 0; i < old.length; i++) old[i].remove();
      if (unseen) bells.forEach(function (id) { document.getElementById(id).insertAdjacentHTML("beforeend", '<span class="badge">' + unseen + '</span>'); });
      document.getElementById("alerts-list").innerHTML = list.length ? list.slice(0, 8).map(function (a) {
        return '<li class="' + (a.seen ? "" : "unseen") + '"><span class="dot"></span><a href="project.html?id=' + a.projectId + '">' + A.esc(a.text) +
          '<time>' + A.ago(a.at) + '</time></a></li>';
      }).join("") : '<li><span></span><span class="muted">No alerts.</span></li>';
    }
    function toggleAlerts(force) {
      var open = typeof force === "boolean" ? force : panel.hidden;
      panel.hidden = !open;
      document.getElementById("bell").setAttribute("aria-expanded", String(open));
    }
    bells.forEach(function (id) { document.getElementById(id).addEventListener("click", function () { toggleAlerts(); }); });
    document.getElementById("alerts-seen").addEventListener("click", function () {
      mine().forEach(function (a) { a.seen = true; });
      A.save(); renderAlerts();
    });
    document.addEventListener("click", function (e) {
      if (!panel.hidden && !panel.contains(e.target) && !e.target.closest("#bell, #tab-alerts")) toggleAlerts(false);
      if (e.target.closest("[data-signout]")) { A.log("Sign out", 0, ""); A.signOut(); }
    });

    /* Find / F key → the page's own search box, else the Projects page */
    function find() {
      var q = document.querySelector("[data-find]");
      if (q) q.focus(); else window.location.href = "projects.html#find";
    }
    document.getElementById("find-btn").addEventListener("click", find);
    document.addEventListener("keydown", function (e) {
      var tag = (e.target.tagName || "").toLowerCase();
      if ((e.key === "f" || e.key === "F") && !e.metaKey && !e.ctrlKey && !e.altKey && tag !== "input" && tag !== "textarea" && tag !== "select" && !document.querySelector("dialog[open]")) {
        e.preventDefault(); find();
      }
    });

    window.SHELL.renderAlerts = renderAlerts;
    window.SHELL.setCrumb = function (text) {
      document.getElementById("crumb").textContent = text || "";
      document.getElementById("crumb-sep").hidden = !text;
    };
    A.paintTheme();
    renderAlerts();

    /* Owner-only page */
    if (document.body.hasAttribute("data-owner-only") && !owner) {
      var main = document.querySelector("main");
      if (main) main.innerHTML = '<div class="empty">Only the owner can open this page.</div>';
      window.SHELL.blocked = true;
    }
    window.SHELL.ready = true;
  });
})();

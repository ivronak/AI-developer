/* Shared page behaviour (no data here — the server renders the data): theme toggle, alerts panel, Find (F key), toasts. */
(function () {
  var SUN = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>';
  var MOON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>';

  function isDark() {
    var t = document.documentElement.getAttribute("data-theme");
    if (t) return t === "dark";
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  }
  function paintTheme() {
    var b = document.querySelectorAll("[data-theme-toggle]");
    for (var i = 0; i < b.length; i++) b[i].innerHTML = isDark() ? SUN : MOON;
  }

  function toast(text) {
    var box = document.getElementById("toasts"); if (!box) return;
    var el = document.createElement("div");
    el.className = "toast"; el.textContent = text;
    box.appendChild(el);
    setTimeout(function () { el.remove(); }, 3400);
  }
  window.UI = { toast: toast };

  document.addEventListener("DOMContentLoaded", function () {
    paintTheme();
    /* server-rendered flash messages fade out like the mock's toasts */
    var flashes = document.querySelectorAll("#toasts .toast");
    for (var f = 0; f < flashes.length; f++) (function (el) { setTimeout(function () { el.remove(); }, 4500); })(flashes[f]);
    /* dialogs: [data-open="id"] opens <dialog id>, [data-close] closes; forms inside close only from ✕ / Cancel */
    document.addEventListener("click", function (e) {
      var o = e.target.closest("[data-open]");
      if (o) { var d = document.getElementById(o.getAttribute("data-open")); if (d && d.showModal) d.showModal(); }
      var c = e.target.closest("dialog [data-close]");
      if (c) c.closest("dialog").close();
    });
    /* a double click must not send a form twice (two saves at once) — lock its buttons after the first submit */
    document.addEventListener("submit", function (e) {
      var f = e.target;
      if (f.method && f.method.toLowerCase() !== "post") return;
      if (f.getAttribute("data-sent")) { e.preventDefault(); return; }
      f.setAttribute("data-sent", "1");
      setTimeout(function () {
        var b = f.querySelectorAll('button[type="submit"], button:not([type])');
        for (var i = 0; i < b.length; i++) b[i].disabled = true;
      }, 0);
    });
    window.addEventListener("pageshow", function () {   /* back button: forms work again */
      var sent = document.querySelectorAll("form[data-sent]");
      for (var i = 0; i < sent.length; i++) {
        sent[i].removeAttribute("data-sent");
        var b = sent[i].querySelectorAll("button"); for (var j = 0; j < b.length; j++) b[j].disabled = false;
      }
    });
    var dlgs = document.querySelectorAll("dialog");
    for (var i = 0; i < dlgs.length; i++) dlgs[i].addEventListener("cancel", function (e) { if (this.querySelector("input, textarea, select")) e.preventDefault(); });
    var panel = document.getElementById("alerts-panel");

    function toggleAlerts(force) {
      if (!panel) return;
      var open = typeof force === "boolean" ? force : panel.hidden;
      panel.hidden = !open;
      var bell = document.getElementById("bell");
      if (bell) bell.setAttribute("aria-expanded", String(open));
    }
    function find() {
      var q = document.querySelector("[data-find]");
      if (q) q.focus();
    }

    document.addEventListener("click", function (e) {
      if (e.target.closest("[data-theme-toggle]")) {
        var next = isDark() ? "light" : "dark";
        document.documentElement.setAttribute("data-theme", next);
        try { localStorage.setItem("apd.theme", next); } catch (err) {}
        paintTheme();
      }
      if (e.target.closest("#bell, #tab-alerts")) { toggleAlerts(); return; }
      if (panel && !panel.hidden && !panel.contains(e.target)) toggleAlerts(false);
      if (e.target.closest("#find-btn")) find();
    });

    document.addEventListener("keydown", function (e) {
      var tag = (e.target.tagName || "").toLowerCase();
      if ((e.key === "f" || e.key === "F") && !e.metaKey && !e.ctrlKey && !e.altKey &&
          tag !== "input" && tag !== "textarea" && tag !== "select" && !document.querySelector("dialog[open]")) {
        if (document.querySelector("[data-find]")) { e.preventDefault(); find(); }
      }
    });
  });
})();

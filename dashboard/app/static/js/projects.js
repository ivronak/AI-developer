/* Projects overview behaviour (data is rendered by the server): filters (KPI tiles + chips), search, card/list view,
   Approvals tabs, and the Owner's approve / reject dialog. */
document.addEventListener("DOMContentLoaded", function () {
  var $ = function (id) { return document.getElementById(id); };
  var filter = "all", view = "grid";
  try { view = localStorage.getItem("apd.view") === "list" ? "list" : "grid"; } catch (e) {}

  var TESTS = {
    all: function () { return true; },
    bad: function (el) { return el.getAttribute("data-level") === "bad"; },
    warn: function (el) { return el.getAttribute("data-level") === "warn"; },
    approval: function (el) { return Number(el.getAttribute("data-waiting")) > 0; },
    open: function (el) { return Number(el.getAttribute("data-open")) > 0; },
    active: function (el) { return el.getAttribute("data-active") === "1"; }
  };

  function render() {
    var q = ($("q").value || "").trim().toLowerCase(), shown = 0;
    var items = document.querySelectorAll("[data-item]");
    for (var i = 0; i < items.length; i++) {
      var el = items[i];
      var vis = TESTS[filter](el) && (!q || el.getAttribute("data-name").indexOf(q) !== -1);
      el.hidden = !vis;
      if (vis && el.classList.contains("pcard")) shown += 1;
    }
    $("grid").hidden = view !== "grid";
    $("list").hidden = view !== "list";
    $("empty").hidden = shown > 0;
    var b = document.querySelectorAll("[data-filter]");
    for (var j = 0; j < b.length; j++) b[j].setAttribute("aria-pressed", String(b[j].getAttribute("data-filter") === filter));
    var v = document.querySelectorAll("[data-view]");
    for (var k = 0; k < v.length; k++) v[k].setAttribute("aria-pressed", String(v[k].getAttribute("data-view") === view));
  }

  document.addEventListener("click", function (e) {
    var f = e.target.closest("[data-filter]");
    if (f) { filter = f.getAttribute("data-filter"); render(); return; }
    var v = e.target.closest("[data-view]");
    if (v) { view = v.getAttribute("data-view"); try { localStorage.setItem("apd.view", view); } catch (err) {} render(); return; }
    var t = e.target.closest("[data-appr]");
    if (t) {
      var key = t.getAttribute("data-appr");
      var tabs = document.querySelectorAll("[data-appr]");
      for (var i = 0; i < tabs.length; i++) tabs[i].setAttribute("aria-pressed", String(tabs[i] === t));
      var lists = document.querySelectorAll("[data-appr-list]");
      for (var j = 0; j < lists.length; j++) lists[j].hidden = lists[j].getAttribute("data-appr-list") !== key;
      return;
    }
    var a = e.target.closest("[data-answer]");
    if (a) {
      var yes = a.getAttribute("data-decision") === "approved";
      $("answer-form").action = a.getAttribute("data-answer");
      $("answer-decision").value = a.getAttribute("data-decision");
      $("answer-title").textContent = (yes ? "Approve" : "Reject") + " for the client";
      $("answer-change").textContent = a.getAttribute("data-title");
      $("answer-project").textContent = a.getAttribute("data-project");
      $("answer-label").textContent = yes ? "How did the client approve? (kept in the activity log)" : "What does the client want changed?";
      $("answer-note").value = "";
      $("answer-note").placeholder = yes ? "e.g. Client said OK on a phone call" : "e.g. Search should also find old entries";
      $("answer-go").textContent = yes ? "Approve" : "Reject";
      $("answer-go").className = "btn " + (yes ? "btn-primary" : "btn-danger");
      $("dlg-answer").showModal();
      return;
    }
    var s = e.target.closest("[data-soon]");
    if (s) UI.toast(s.getAttribute("data-soon"));
  });
  $("q").addEventListener("input", render);
  if (window.location.hash === "#find") $("q").focus();
  render();
});

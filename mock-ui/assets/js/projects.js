/* S2 Projects overview: KPI tiles, three compact panels (health · activity log · approvals with approve / reject), colourful project cards / list. */
document.addEventListener("DOMContentLoaded", function () {
  if (!window.SHELL || window.SHELL.blocked) return;
  var D = window.DEMO, A = window.APP, I = A.ICONS, user = window.SHELL.user;
  var $ = function (id) { return document.getElementById(id); };
  var LABEL = { bad: "Down", warn: "Attention", ok: "Healthy" };
  var RANK = { bad: 0, warn: 1, ok: 2 };
  var ALERT_IC = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0zM12 9v4M12 17h.01"/></svg>';
  var filter = "all";
  var apprTab = "waiting";
  var view = A.get("apd.view") || "grid";
  var mine = A.myProjects(user);

  var hue = A.hue;

  var rows = [];
  function buildRows() {
    rows = mine.map(function (p) {
      var ch = A.changesOf(p.id), ld = A.lastDeploy(p.id), lb = A.lastBackup(p.id);
      return {
        p: p, h: A.health(p), ld: ld,
        open: ch.filter(function (c) { return c.status !== "live"; }).length,
        waiting: ch.filter(function (c) { return c.status === "client"; }).length,
        backupAt: lb ? lb.at : null
      };
    });
  }
  buildRows();

  var FILTERS = [
    { key: "all", label: "All", test: function () { return true; } },
    { key: "bad", label: "Down", test: function (r) { return r.h.level === "bad"; } },
    { key: "warn", label: "Attention", test: function (r) { return r.h.level === "warn"; } },
    { key: "approval", label: "Approval pending", test: function (r) { return r.waiting > 0; } },
    { key: "open", label: "Open changes", test: function (r) { return r.open > 0; } },
    { key: "active", label: "Active", test: function (r) { return r.p.active; } }
  ];
  function count(key) { return rows.filter(FILTERS.filter(function (f) { return f.key === key; })[0].test).length; }

  var initials = A.initials;
  function host(url) { return url.replace("https://", ""); }
  function staleBackup(r) { return !r.backupAt || A.minSince(r.backupAt) > D.BACKUP_STALE_MIN; }

  $("search-icon").innerHTML = I.search;
  document.querySelector('[data-view="grid"]').innerHTML = I.grid;
  document.querySelector('[data-view="list"]').innerHTML = I.list;
  $("add-new").insertAdjacentHTML("afterbegin", I.plus);
  var hic = document.querySelectorAll(".h-ic[data-icon]");
  for (var i = 0; i < hic.length; i++) hic[i].innerHTML = I[hic[i].getAttribute("data-icon")];
  if (!window.SHELL.owner) {
    $("scope-note").textContent = "You see only the " + rows.length + " projects assigned to you.";
    $("scope-note").hidden = false;
  }

  /* ---------- KPI layer ---------- */
  function renderKpis() {
    var openTotal = rows.reduce(function (n, r) { return n + r.open; }, 0);
    var waitTotal = rows.reduce(function (n, r) { return n + r.waiting; }, 0);
    var K = [
      ["all", 240, "grid", rows.length, "Projects"],
      ["bad", 0, "bell", count("bad"), "Down now"],
      ["warn", 35, "activity", count("warn"), "Need attention"],
      ["approval", 215, "send", waitTotal, "Waiting approval"],
      ["open", 265, "list", openTotal, "Open changes"]
    ];
    $("kpis").innerHTML = K.map(function (k) {
      return '<button class="kpi" type="button" style="--h:' + k[1] + '" data-filter="' + k[0] + '" aria-pressed="' + (filter === k[0]) + '">' +
        '<span class="kpi-ic">' + I[k[2]] + '</span><b>' + k[3] + '</b><span class="l">' + k[4] + '</span></button>';
    }).join("");
  }

  /* ---------- side column ---------- */
  function renderHealth() {
    var n = { ok: 0, warn: 0, bad: 0 };
    rows.forEach(function (r) { n[r.h.level] += 1; });
    var total = rows.length || 1;
    $("hbar").innerHTML = ["bad", "warn", "ok"].filter(function (k) { return n[k]; }).map(function (k) {
      return '<span class="s-' + k + '" style="width:' + (n[k] / total * 100) + '%"></span>';
    }).join("");
    $("hcounts").innerHTML = [["ok", "Healthy"], ["warn", "Attention"], ["bad", "Down"]].map(function (x) {
      return '<div class="hcount s-' + x[0] + '"><b>' + n[x[0]] + '</b><span>' + x[1] + '</span></div>';
    }).join("");
    $("attention").innerHTML = rows.filter(function (r) { return r.h.level !== "ok"; })
      .sort(function (a, b) { return RANK[a.h.level] - RANK[b.h.level]; })
      .map(function (r) {
        return '<li><a class="s-' + r.h.level + '" href="project.html?id=' + r.p.id + '" title="' + A.esc(r.h.reason) + '"><span class="ldot s-' + r.h.level + '"></span><strong>' + A.esc(r.p.name) + '</strong>' +
          '<span class="r">' + A.esc(r.h.reason) + '</span></a></li>';
      }).join("");
  }

  function dayLabel(ts) {
    var d = new Date(ts), t = new Date();
    if (d.toDateString() === t.toDateString()) return "Today";
    t.setDate(t.getDate() - 1);
    if (d.toDateString() === t.toDateString()) return "Yesterday";
    return A.stamp(ts).split(",")[0];
  }

  /* Activity log: who did what last, grouped by day */
  function renderActivity() {
    var ICON = {
      "Go Live": ["rocket", 150], "Rollback": ["undo", 24], "Client approved": ["check", 150], "Client asked changes": ["activity", 35],
      "Approved for client": ["check", 150], "Changes for client": ["x", 35],
      "Sent to client": ["send", 215], "Prompt": ["activity", 265], "Backup": ["db", 175], "Restore": ["db", 24],
      "Sync": ["sync", 199], "Beta deploy": ["upload", 220], "Alert": ["bell", 0], "Tests": ["check", 160], "Settings": ["activity", 240]
    };
    var list = A.S().log.filter(function (l) { return l.projectId && A.canSee(user, l.projectId); }).slice(0, 14);
    var html = "", last = "";
    list.forEach(function (l) {
      var day = dayLabel(l.at);
      if (day !== last) { html += '<li class="act-day">' + day + '</li>'; last = day; }
      var who = l.userId ? A.userName(l.userId) : (l.action.indexOf("Client") === 0 ? "Client" : "Monitor");
      var ic = ICON[l.action] || ["activity", 240];
      html += '<li><span class="act-ic" style="--h:' + ic[1] + '">' + I[ic[0]] + '</span><span><span class="t"><b>' + A.esc(who) + '</b> · ' + A.esc(l.action) +
        ' · ' + A.esc(A.project(l.projectId).name) + '</span>' + (l.details ? '<span class="act-det">' + A.esc(l.details) + '</span>' : '') + '<time>' + A.ago(l.at) + '</time></span></li>';
    });
    $("activity").innerHTML = html || '<li class="mini-empty">Nothing yet.</li>';
    $("log-link").hidden = !window.SHELL.owner;
  }

  /* Approvals: links waiting for the client (owner can record the client's answer here) + approved, ready to go live */
  function renderApprovals() {
    var all = A.S().changes.filter(function (c) { return A.canSee(user, c.projectId) && (c.status === "client" || c.status === "approved"); });
    var waiting = all.filter(function (c) { return c.status === "client"; }).sort(function (a, b) { return a.approval.sentAt - b.approval.sentAt; });
    var ready = all.filter(function (c) { return c.status === "approved"; });
    $("appr-tabs").innerHTML =
      '<button type="button" data-appr="waiting" aria-pressed="' + (apprTab === "waiting") + '">Pending ' + waiting.length + '</button>' +
      '<button type="button" data-appr="ready" aria-pressed="' + (apprTab === "ready") + '">Approved ' + ready.length + '</button>';
    var list = apprTab === "waiting" ? waiting : ready;
    $("approvals").innerHTML = list.length ? list.map(function (c) {
      var p = A.project(c.projectId), a = c.approval;
      var slow = apprTab === "waiting" && A.minSince(a.sentAt) > D.APPROVAL_SLOW_MIN;
      var acts = apprTab === "waiting"
        ? (window.SHELL.owner
            ? '<div class="appr-acts"><button class="btn btn-ok" type="button" data-approve="' + c.id + '">' + I.check + 'Approve</button>' +
              '<button class="btn btn-no" type="button" data-reject="' + c.id + '">' + I.x + 'Reject</button>' +
              '<a class="btn" href="' + window.location.href.replace(/projects\.html.*$/, "") + 'approve.html?t=' + a.token + '" target="_blank" rel="noopener">' + I.ext + 'Client page</a></div>'
            : '<p class="hint">Waiting for the client. Only the owner can record an answer.</p>')
        : '<div class="appr-acts"><a class="btn btn-primary" href="project.html?id=' + p.id + '#chat">' + I.rocket + 'Go Live…</a></div>';
      return '<li class="appr" style="--h:' + hue(p) + '"><div class="appr-top"><a href="project.html?id=' + p.id + '">' + A.esc(c.title) + '</a>' +
        '<span class="appr-age' + (slow ? " slow" : "") + '">' + (apprTab === "waiting" ? "sent " + A.ago(a.sentAt) : "approved " + A.ago(a.decidedAt)) + '</span></div>' +
        '<div class="appr-meta"><span class="pdot" style="--h:' + hue(p) + '"></span>' + A.esc(p.name) + ' · ' + A.esc(p.client) + (a.comment && apprTab === "ready" ? ' · “' + A.esc(a.comment) + '”' : '') + '</div>' +
        acts + '</li>';
    }).join("") : '<li class="mini-empty">' + (apprTab === "waiting" ? "No approvals pending." : "Nothing approved and waiting to go live.") + '</li>';
  }

  /* Owner records the client's answer (e.g. the client confirmed on a call). Always with a note, always logged. */
  function answer(changeId, yes) {
    var c = A.S().changes.filter(function (x) { return x.id === changeId; })[0];
    if (!c || !window.SHELL.owner) return;
    var p = A.project(c.projectId);
    A.dialog({
      title: (yes ? "Approve" : "Reject") + " for the client",
      body: '<p><strong>' + A.esc(c.title) + '</strong><br><span class="hint">' + A.esc(p.name) + ' · ' + A.esc(p.client) + '</span></p>' +
        '<div class="field"><label for="ans-note">' + (yes ? "How did the client approve? (kept in the activity log)" : "What does the client want changed?") + '</label>' +
        '<textarea id="ans-note" placeholder="' + (yes ? "e.g. Client said OK on a phone call" : "e.g. Search should also find old entries") + '"></textarea></div>' +
        '<div class="msg msg-bad" id="ans-err" hidden></div>',
      actions: [{ label: yes ? "Approve" : "Reject", kind: yes ? "primary" : "danger", onClick: function (d) {
        var note = d.querySelector("#ans-note").value.trim();
        if (!note) { var e = d.querySelector("#ans-err"); e.textContent = yes ? "Write how the client approved." : "Write what should change."; e.hidden = false; return false; }
        var now = Date.now();
        c.approval.decision = yes ? "approved" : "changes";
        c.approval.decidedAt = now;
        c.approval.comment = note;
        c.status = yes ? "approved" : "changes";
        if (!yes) c.chat.push({ from: "owner", by: user.id, at: now, text: "Client asked for changes: " + note });
        A.save();
        A.log(yes ? "Approved for client" : "Changes for client", p.id, c.title + " — " + note);
        A.toast(yes ? "Approved. It is ready to go live." : "Sent back to beta with the client's note.");
        refresh();
      } }]
    });
  }

  /* ---------- projects ---------- */
  function renderChips() {
    $("chips").innerHTML = FILTERS.map(function (f) {
      return '<button type="button" data-filter="' + f.key + '" aria-pressed="' + (filter === f.key) + '">' +
        f.label + ' <span class="n">' + rows.filter(f.test).length + '</span></button>';
    }).join("");
  }

  function techChips(p) {
    return p.tech.map(function (t) { return '<span class="tech" style="--h:' + A.techHue(t) + '">' + t + '</span>'; }).join("");
  }

  function nowLayer(r) {
    if (r.h.level !== "ok") {
      return '<div class="pc-now s-' + r.h.level + '">' + ALERT_IC + '<span>' + A.esc(r.h.reason) +
        '<small>' + (r.h.level === "bad" ? "Open the project to roll back or debug" : "Needs a look") + '</small></span></div>';
    }
    return '<div class="pc-now">' + I.rocket + '<span>' + A.esc(r.ld ? r.ld.note : "No live release yet") +
      '<small>Live on <span class="mono">' + r.p.live.commit + '</span></small></span></div>';
  }

  function stat(h, icon, value, label, cls) {
    return '<div class="stat' + (cls ? " " + cls : "") + '" style="--h:' + h + '"><i>' + I[icon] + '</i><b>' + value + '</b><span>' + label + '</span></div>';
  }

  function card(r) {
    var p = r.p, stale = staleBackup(r);
    return '<a class="pcard" href="project.html?id=' + p.id + '" style="--h:' + hue(p) + '">' +
      '<div class="pc-band"><span class="pav' + (p.active ? "" : " off") + '">' + initials(p.name) + '</span>' +
        '<span class="pc-id"><strong>' + A.esc(p.name) + '</strong><span>' + host(p.live.url) + '</span></span>' +
        '<span class="hpill s-' + r.h.level + '">' + LABEL[r.h.level] + '</span></div>' +
      '<div class="pc-body">' +
        '<div class="chip-row"><span class="repo">' + I.github + '<span>' + A.esc(p.repo) + '</span></span>' + techChips(p) +
          (p.active ? "" : '<span class="tag-off">Dormant</span>') + '</div>' +
        nowLayer(r) +
        '<div class="stats">' +
          stat(220, "upload", r.ld ? A.ago(r.ld.at) : "Never", "Last deploy") +
          stat(stale ? 0 : 160, "db", r.backupAt ? A.ago(r.backupAt) : "Missing", "Last backup", stale ? "alarm" : "") +
          stat(265, "list", r.open, "Open changes", r.open ? "" : "zero") +
          stat(215, "send", r.waiting, "Waiting approval", r.waiting ? "" : "zero") +
        '</div>' +
        '<div class="pc-foot"><span>' + A.esc(p.client) + ' · ' + p.host + '</span><span class="open">Open project →</span></div>' +
      '</div>' +
    '</a>';
  }

  function listRow(r) {
    var p = r.p, stale = staleBackup(r);
    return '<a class="prow" href="project.html?id=' + p.id + '" style="--h:' + hue(p) + '">' +
      '<span class="who"><span class="pav' + (p.active ? "" : " off") + '">' + initials(p.name) + '</span><span class="nm"><strong>' + A.esc(p.name) + '</strong><span>' + A.esc(p.client) + ' · ' + p.tech.join(" + ") + '</span></span></span>' +
      '<span class="col"><span class="st s-' + r.h.level + '"><span class="ldot s-' + r.h.level + '"></span>' + LABEL[r.h.level] + '</span>' + (r.h.reason ? '<span class="sub">' + A.esc(r.h.reason) + '</span>' : '') + '</span>' +
      '<span class="col col-hide"><span class="commit">' + I.branch + '<span class="mono">' + p.live.commit + '</span></span><span class="sub">' + A.esc(r.ld ? r.ld.note : "") + '</span></span>' +
      '<span class="col col-hide"><span class="sub' + (stale ? " stale" : "") + '">' + (r.backupAt ? A.ago(r.backupAt) : "missing") + '</span></span>' +
      '<span class="col col-hide"><span>' + (r.ld ? A.ago(r.ld.at) : "—") + '</span><span class="sub">' + r.open + ' open · ' + r.waiting + ' waiting</span></span>' +
    '</a>';
  }

  function render() {
    var f = FILTERS.filter(function (x) { return x.key === filter; })[0];
    var q = $("q").value.trim().toLowerCase();
    var list = rows.filter(f.test).filter(function (r) {
      return !q || r.p.name.toLowerCase().indexOf(q) !== -1 || r.p.client.toLowerCase().indexOf(q) !== -1;
    }).sort(function (a, b) {
      return RANK[a.h.level] - RANK[b.h.level] || a.p.name.localeCompare(b.p.name);
    });
    var vb = document.querySelectorAll("[data-view]");
    for (var i = 0; i < vb.length; i++) vb[i].setAttribute("aria-pressed", String(vb[i].getAttribute("data-view") === view));
    if (view === "list") {
      $("grid").className = "plist";
      $("grid").innerHTML = list.length ? '<div class="plist-head"><span>Project</span><span>Status</span><span>Live version</span><span>Backup</span><span>Last deploy</span></div>' + list.map(listRow).join("") : "";
    } else {
      $("grid").className = "pgrid";
      $("grid").innerHTML = list.map(card).join("");
    }
    $("grid").hidden = !list.length;
    $("empty").hidden = list.length > 0;
  }

  function setFilter(key) { filter = key; renderKpis(); renderChips(); render(); }
  function refresh() { buildRows(); renderKpis(); renderHealth(); renderActivity(); renderApprovals(); renderChips(); render(); }

  document.addEventListener("click", function (e) {
    var b = e.target.closest("#chips [data-filter], #kpis [data-filter]");
    if (b) setFilter(b.getAttribute("data-filter"));
    var t = e.target.closest("[data-appr]");
    if (t) { apprTab = t.getAttribute("data-appr"); renderApprovals(); }
    var ok = e.target.closest("[data-approve]"), no = e.target.closest("[data-reject]");
    if (ok) answer(Number(ok.getAttribute("data-approve")), true);
    if (no) answer(Number(no.getAttribute("data-reject")), false);
  });
  document.querySelector(".view-toggle").addEventListener("click", function (e) {
    var b = e.target.closest("[data-view]");
    if (!b) return;
    view = b.getAttribute("data-view"); A.set("apd.view", view); render();
  });
  $("q").addEventListener("input", render);
  $("add-new").addEventListener("click", function () { A.toast("Adding a project from the dashboard is planned for v2 (S10)."); });
  if (window.location.hash === "#find") $("q").focus();

  refresh();
});

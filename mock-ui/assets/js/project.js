/* S3 Project detail · S4 Chatbot / changes · S6 Go Live + Rollback · sync, beta deploy, deploys, backups, settings (F13). */
document.addEventListener("DOMContentLoaded", function () {
  if (!window.SHELL || window.SHELL.blocked) return;
  var A = window.APP, B = window.BACKUPS, I = A.ICONS, user = window.SHELL.user, owner = window.SHELL.owner;
  var $ = function (id) { return document.getElementById(id); };

  var p = A.project(A.qs("id"));
  if (!p || !A.canSee(user, p.id)) {
    $("main").innerHTML = '<div class="empty">' + (p ? "This project is not assigned to you." : "Project not found.") + '</div>';
    return;
  }
  document.title = p.name + " · AI Project Dashboard";

  var STEPS = [["beta", "In beta"], ["passed", "Tests passed"], ["client", "With client"], ["approved", "Approved"], ["live", "Live"]];
  var current = null;            // change id shown in Chat; null = new change
  var tab = "chat";
  var attach = null;             // { name } chosen in the composer
  var busy = false;              // Claude (mock) is replying

  function statusInfo(c) {
    if (c.status === "beta") return c.tests && c.tests[0] < c.tests[1] ? ["Tests failing", "warn"] : ["In beta", "accent"];
    if (c.status === "passed") return ["Tests passed", "ok"];
    if (c.status === "client") return ["With client", "accent"];
    if (c.status === "changes") return ["Client asked for changes", "warn"];
    if (c.status === "approved") return ["Approved", "ok"];
    return ["Live", ""];
  }
  function pill(c) { var s = statusInfo(c); return '<span class="pill' + (s[1] ? " pill-" + s[1] : "") + '">' + s[0] + '</span>'; }
  function change(id) { return A.S().changes.filter(function (c) { return c.id === id; })[0] || null; }
  function testsTotal() { return 20 + p.id * 2; }
  function filesFor() {
    return p.tech.indexOf("Flask") !== -1
      ? ["app/routes.py", "app/templates/page.html", "tests/test_pages.py"]
      : ["app/Http/Controllers/PageController.php", "resources/views/pages/show.blade.php", "tests/Feature/PageTest.php"];
  }
  function approvalLink(c) {
    var base = window.location.href.replace(/project\.html.*$/, "");
    return base + "approve.html?t=" + c.approval.token;
  }

  /* ---------- header ---------- */
  function renderHead() {
    window.SHELL.setCrumb(p.name);
    $("hero").style.setProperty("--h", A.hue(p));
    $("p-av").textContent = A.initials(p.name);
    if (!p.active) $("p-av").classList.add("off");
    $("p-name").textContent = p.name;
    $("p-sub").innerHTML = '<span>' + A.esc(p.client) + ' · ' + p.host + (p.active ? "" : " · Dormant") + '</span>' +
      '<a href="' + p.live.url + '" target="_blank" rel="noopener">' + p.live.url.replace("https://", "") + '</a>';
    $("p-chips").innerHTML = '<span class="repo">' + I.github + '<span>' + A.esc(p.repo) + '</span></span>' +
      p.tech.map(function (t) { return '<span class="tech" style="--h:' + A.techHue(t) + '">' + t + '</span>'; }).join("");
    renderHeadHealth();
  }
  function renderEnvs() {
    var ld = A.lastDeploy(p.id);
    $("beta-commit").textContent = p.beta.commit;
    $("beta-url").textContent = p.beta.url.replace("https://", ""); $("beta-url").href = p.beta.url;
    var mm = { off: "Messages off", test: "Messages to test numbers only", on: "Messages ON (real users)" }[p.beta.msgMode];
    $("beta-meta").innerHTML = "Synced from live " + A.ago(p.beta.syncAt) + " · " + mm + " · Payments " + (p.beta.payMode === "sandbox" ? "sandbox" : "<strong>LIVE</strong>");
    $("live-commit").textContent = p.live.commit;
    $("live-url").textContent = p.live.url.replace("https://", ""); $("live-url").href = p.live.url;
    $("live-meta").textContent = ld ? "Deployed " + A.ago(ld.at) + " by " + A.userName(ld.by) : "Never deployed";
  }
  function refreshTop() { renderEnvs(); renderHeadHealth(); if (window.SHELL.renderAlerts) window.SHELL.renderAlerts(); }
  var ALERT_IC = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0zM12 9v4M12 17h.01"/></svg>';
  function renderHeadHealth() {
    var h = A.health(p), label = { bad: "Down", warn: "Attention", ok: "Healthy" }[h.level];
    $("p-pill").className = "hpill s-" + h.level;
    $("p-pill").textContent = label;
    $("p-now").hidden = h.level === "ok";
    $("p-now").className = "pc-now s-" + h.level;
    $("p-now").innerHTML = h.level === "ok" ? "" : ALERT_IC + '<span>' + A.esc(h.reason) + '<small>' +
      (h.level === "bad" ? "Roll back to the last good version, or ask Claude to debug it on beta." : "Needs a look.") + '</small></span>';
    renderKpis();
  }
  function renderKpis() {
    var ch = A.changesOf(p.id), ld = A.lastDeploy(p.id), lb = A.lastBackup(p.id);
    var open = ch.filter(function (c) { return c.status !== "live"; }).length;
    var wait = ch.filter(function (c) { return c.status === "client"; }).length;
    var stale = !lb || A.minSince(lb.at) > window.DEMO.BACKUP_STALE_MIN;
    function k(h, icon, val, label, cls) {
      return '<div class="kpi static' + (cls ? " " + cls : "") + '" style="--h:' + h + '"><span class="kpi-ic">' + I[icon] + '</span><b class="sm">' + val + '</b><span class="l">' + label + '</span></div>';
    }
    $("p-kpis").innerHTML =
      k(265, "list", open, "Open changes") +
      k(215, "send", wait, "Waiting approval") +
      k(220, "upload", ld ? A.ago(ld.at) : "Never", "Last deploy") +
      k(stale ? 0 : 160, "db", lb ? A.ago(lb.at) : "Missing", "Last backup", stale ? "alarm" : "");
  }

  var iconEls = document.querySelectorAll("[data-icon]");
  for (var i = 0; i < iconEls.length; i++) iconEls[i].insertAdjacentHTML("afterbegin", I[iconEls[i].getAttribute("data-icon")]);
  if (!owner) {
    $("act-live").disabled = true; $("act-rollback").disabled = true; $("owner-note").hidden = false;
  }

  /* ---------- tabs ---------- */
  function setTab(t) {
    tab = t;
    var bs = $("tabs").querySelectorAll("button");
    for (var i = 0; i < bs.length; i++) bs[i].setAttribute("aria-selected", String(bs[i].getAttribute("data-tab") === t));
    try { history.replaceState(null, "", "#" + t); } catch (e) {}
    render();
  }
  $("tabs").addEventListener("click", function (e) {
    var b = e.target.closest("[data-tab]");
    if (b) setTab(b.getAttribute("data-tab"));
  });

  function render() {
    if (tab === "chat") renderChat();
    else if (tab === "changes") renderChanges();
    else if (tab === "deploys") renderDeploys();
    else if (tab === "backups") renderBackups();
    else renderSettings();
  }

  /* ---------- S4 Chat ---------- */
  function openChanges() {
    return A.changesOf(p.id).filter(function (c) { return c.status !== "live"; }).sort(function (a, b) { return b.at - a.at; });
  }

  function stepper(c) {
    var idx = { beta: 0, passed: 1, client: 2, changes: 2, approved: 3, live: 4 }[c.status];
    return '<ol class="stepper" aria-label="Progress">' + STEPS.map(function (s, k) {
      var cls = k < idx ? "done" : k === idx ? ("now" + (c.status === "changes" || (c.status === "beta" && c.tests && c.tests[0] < c.tests[1]) ? " warn" : "")) : "";
      if (c.status === "live") cls = "done";
      return '<li class="' + cls + '">' + s[1] + '</li>';
    }).join("") + '</ol>';
  }

  function changeCard(c) {
    var t = c.tests, acts = "";
    var html = '<article class="ccard">' +
      '<div class="ccard-head"><div><h2>' + A.esc(c.title) + '</h2><p class="muted">Change #' + c.id + ' · started ' + A.ago(c.at) + ' by ' + A.esc(A.userName(c.by)) + '</p></div>' + pill(c) + '</div>' +
      stepper(c);
    if (t) html += '<p class="tests ' + (t[0] === t[1] ? "tests-ok" : "tests-bad") + '">Tests: ' + t[0] + ' / ' + t[1] + ' passed</p>';
    if (c.files && c.files.length) html += '<div><div class="label">Files changed on beta</div><ul class="files">' + c.files.map(function (f) { return '<li>' + A.esc(f) + '</li>'; }).join("") + '</ul></div>';

    if (c.status === "beta") {
      acts = '<button class="btn btn-primary btn-sm" type="button" data-do="tests">' + (t && t[0] < t[1] ? "Ask Claude to fix and re-run tests" : "Run tests on beta") + '</button>';
    } else if (c.status === "passed") {
      acts = '<button class="btn btn-primary btn-sm" type="button" data-do="send">Send to client</button>' +
        '<a class="btn btn-sm" href="' + p.beta.url + '" target="_blank" rel="noopener">' + I.ext + 'Open beta</a>';
    } else if (c.status === "client") {
      var a = c.approval, left = Math.max(0, window.DEMO.LINK_DAYS - Math.floor(A.minSince(a.sentAt) / 1440));
      var link = approvalLink(c);
      var wa = "https://wa.me/?text=" + encodeURIComponent("Hi, please check this change on the test site and approve it: " + link);
      html += '<div class="share"><div>Sent ' + A.ago(a.sentAt) + ' · link closes in ' + left + (left === 1 ? " day" : " days") + ' or after the client replies.</div>' +
        '<div class="share-link" id="share-link">' + A.esc(link) + '</div>' +
        '<div class="ccard-acts"><button class="btn btn-sm" type="button" data-do="copy">' + I.copy + 'Copy link</button>' +
        '<a class="btn btn-sm" href="' + wa + '" target="_blank" rel="noopener">WhatsApp</a>' +
        '<a class="btn btn-sm" href="' + link + '" target="_blank" rel="noopener">' + I.ext + 'Open as client</a></div></div>';
    } else if (c.status === "changes") {
      html += '<div class="client-said"><strong>Client asked for changes:</strong> ' + A.esc(c.approval.comment) + '</div>';
      acts = '<span class="hint">Tell Claude what to change below. The old client link is closed.</span>';
    } else if (c.status === "approved") {
      html += '<p class="reason reason-ok">Client approved ' + A.ago(c.approval.decidedAt) + '.</p>';
      acts = owner ? '<button class="btn btn-primary btn-sm" type="button" data-do="golive">' + I.rocket + 'Go Live</button>' : '<span class="hint">Waiting for the owner to go live.</span>';
    } else if (c.status === "live") {
      html += '<p class="reason reason-ok">Went live ' + (c.liveAt ? A.ago(c.liveAt) : "") + '.</p>';
    }
    if (acts) html += '<div class="ccard-acts">' + acts + '</div>';
    return html + '</article>';
  }

  function bubble(m) {
    var cls = m.from === "owner" ? "b-owner" : m.from === "client" ? "b-client" : "b-claude";
    var who = m.from === "owner" ? A.userName(m.by || 1) : m.from === "client" ? "Client" : "Claude";
    return '<div class="bubble ' + cls + (m.typing ? " b-typing" : "") + '"><span class="who">' + A.esc(who) + '</span>' +
      '<span>' + A.esc(m.text) + '</span>' +
      (m.shot ? '<span class="attach-chip">' + I.clip + A.esc(m.shot) + '</span>' : '') +
      (m.at ? '<time>' + A.ago(m.at) + '</time>' : '') + '</div>';
  }

  function renderChat() {
    var list = openChanges(), c = current ? change(current) : null;
    var html = '<div class="pick" role="group" aria-label="Pick a change">' +
      '<button type="button" data-pick="" aria-pressed="' + (!c) + '">' + I.plus + '<span class="t">New change</span></button>' +
      list.map(function (x) {
        var s = statusInfo(x);
        return '<button type="button" data-pick="' + x.id + '" aria-pressed="' + (c && c.id === x.id) + '"><span class="sdot' + (s[1] ? " sdot-" + s[1] : "") + '"></span><span class="t">' + A.esc(x.title) + '</span></button>';
      }).join("") + '</div>';

    if (c) {
      html += changeCard(c) + '<div class="thread" id="thread">' + c.chat.map(bubble).join("") + (busy ? bubble({ from: "claude", text: "Working on beta…", typing: true }) : "") + '</div>';
    } else {
      html += '<p class="intro">Describe the change the client asked for. Add a screenshot if it helps. Claude makes it on <strong>beta only</strong> and runs the tests — live is never touched.</p>';
    }

    var locked = c && (c.status === "approved" || c.status === "live");
    html += locked
      ? '<p class="composer-note">This change is ' + (c.status === "live" ? "live" : "approved") + '. For anything else, start a new change.</p>'
      : '<form class="composer" id="composer">' +
          '<label for="msg" class="label" hidden>Message to Claude</label>' +
          '<textarea id="msg" placeholder="' + (c ? "Tell Claude what to adjust…" : "e.g. Client wants the logo bigger on the login page") + '"' + (busy ? " disabled" : "") + '></textarea>' +
          '<div class="composer-bar">' +
            '<div><input type="file" id="shot" accept="image/*" hidden><button class="btn btn-sm" type="button" id="attach-btn">' + I.clip + 'Screenshot</button> ' +
            (attach ? '<span class="attach-chip">' + A.esc(attach.name) + ' <button class="link-btn" type="button" id="attach-x" aria-label="Remove screenshot">✕</button></span>' : '') + '</div>' +
            '<button class="btn btn-primary btn-sm" type="submit"' + (busy ? " disabled" : "") + '>' + I.send + (c ? "Send" : "Start change") + '</button>' +
          '</div>' +
        '</form>';
    $("panel").innerHTML = html;

    var form = $("composer");
    if (form) {
      form.addEventListener("submit", function (e) { e.preventDefault(); send(); });
      $("attach-btn").addEventListener("click", function () { $("shot").click(); });
      $("shot").addEventListener("change", function () {
        if (this.files && this.files[0]) { attach = { name: this.files[0].name }; var txt = $("msg").value; renderChat(); $("msg").value = txt; }
      });
      if ($("attach-x")) $("attach-x").addEventListener("click", function () { attach = null; var txt = $("msg").value; renderChat(); $("msg").value = txt; });
    }
  }

  function makeTitle(text) {
    var t = text.split(/[.\n]/)[0].trim();
    if (t.length > 60) t = t.slice(0, 57).replace(/\s+\S*$/, "") + "…";
    return t.charAt(0).toUpperCase() + t.slice(1);
  }

  function send() {
    var text = $("msg").value.trim();
    if (!text) { $("msg").focus(); return; }
    var s = A.S(), c = current ? change(current) : null, now = Date.now();
    var msg = { from: "owner", by: user.id, at: now, text: text, shot: attach ? attach.name : null };
    if (!c) {
      c = { id: s.changes.reduce(function (m, x) { return Math.max(m, x.id); }, 0) + 1, projectId: p.id, title: makeTitle(text), status: "beta", at: now, by: user.id, files: [], tests: null, approval: null, chat: [msg] };
      s.changes.push(c);
      current = c.id;
      A.log("Prompt", p.id, "New change: " + c.title, text);
    } else {
      c.chat.push(msg);
      c.status = "beta";
      if (c.approval && c.approval.decision !== "approved") c.approval = null;
      A.log("Prompt", p.id, "Change #" + c.id + ": follow-up", text);
    }
    attach = null; busy = true; A.save(); renderChat();
    var id = c.id;
    setTimeout(function () {
      var x = change(id); if (!x) return;
      var n = testsTotal();
      x.files = x.files && x.files.length ? x.files : filesFor();
      x.tests = [n, n];
      x.status = "passed";
      x.chat.push({ from: "claude", at: Date.now(), text: "Done on beta. I changed " + x.files.length + " files and all " + n + " tests passed. Please check it on beta, then send it to the client." });
      busy = false; A.save();
      if (tab === "chat") renderChat();
      refreshTop();
    }, 1800);
  }

  $("panel").addEventListener("click", function (e) {
    var pk = e.target.closest("[data-pick]");
    if (pk) { current = pk.getAttribute("data-pick") ? Number(pk.getAttribute("data-pick")) : null; attach = null; renderChat(); return; }
    var d = e.target.closest("[data-do]");
    if (!d) return;
    var c = change(current), what = d.getAttribute("data-do");
    if (what === "tests") runTests(c);
    else if (what === "send") sendToClient(c);
    else if (what === "copy") A.copy(approvalLink(c), "Link copied.");
    else if (what === "golive") goLive();
  });

  function runTests(c) {
    A.dialog({
      title: "Run tests on beta",
      body: '<p>Claude ' + (c.tests && c.tests[0] < c.tests[1] ? "fixes the failing tests, then " : "") + 'runs all automated tests on beta.</p><div data-run></div>',
      actions: [{ label: "Start", kind: "primary", onClick: function (d) {
        d.querySelector("[data-act]").disabled = true;
        var n = testsTotal();
        A.runSteps(d.querySelector("[data-run]"), (c.tests && c.tests[0] < c.tests[1] ? ["Read the failing tests", "Fix the code on beta"] : ["Finish the change on beta"]).concat(["Run " + n + " tests"]), function () {
          c.tests = [n, n]; c.status = "passed";
          if (!c.files || !c.files.length) c.files = filesFor();
          c.chat.push({ from: "claude", at: Date.now(), text: "All " + n + " tests passed on beta. Ready to send to the client." });
          A.save(); A.log("Tests", p.id, "Change #" + c.id + ": " + n + "/" + n + " passed");
          d.close(); renderChat(); refreshTop();
        });
        return false;
      } }]
    });
  }

  function sendToClient(c) {
    var list = ["Open the test site", "Try: " + c.title, "Check that nothing else looks different"].join("\n");
    A.dialog({
      title: "Send to client",
      body: '<p>Creates a one-time link for the client. No login needed. It closes after 7 days or once the client replies.</p>' +
        '<div class="field"><label for="checklist">What the client should check (one per line)</label><textarea id="checklist">' + A.esc(list) + '</textarea></div>',
      actions: [{ label: "Create link", kind: "primary", onClick: function (d) {
        var items = d.querySelector("#checklist").value.split("\n").map(function (x) { return x.trim(); }).filter(Boolean);
        c.approval = { token: A.token(), sentAt: Date.now(), checklist: items, decision: null, comment: "", decidedAt: null };
        c.status = "client";
        A.save(); A.log("Sent to client", p.id, c.title);
        A.toast("Link ready. Copy it or share on WhatsApp.");
        renderChat();
      } }]
    });
  }

  /* ---------- S6 Go Live ---------- */
  function goLive() {
    if (!owner) return;
    var ch = A.changesOf(p.id);
    var approved = ch.filter(function (c) { return c.status === "approved"; });
    var unapproved = ch.filter(function (c) { return c.status === "passed" || c.status === "client" || c.status === "changes"; });
    var snap = A.lastSnapshot(p.id);
    var body = '<div><div class="label">Goes live</div>' + (approved.length
      ? '<ul class="summary-list">' + approved.map(function (c) { return '<li>' + A.esc(c.title) + ' <span class="pill pill-ok">Approved</span></li>'; }).join("") + '</ul>'
      : '<p class="msg msg-warn">No change is approved by the client yet.</p>') + '</div>';
    if (unapproved.length) {
      body += '<div class="panel-box"><label class="check"><input type="checkbox" id="override"> <span>Also send changes the client has <strong>not</strong> approved (' + unapproved.length + ')</span></label>' +
        '<div class="field" id="reason-box" hidden><label for="reason">Reason (kept in the activity log)</label><textarea id="reason" placeholder="e.g. Client confirmed on a phone call"></textarea></div></div>';
    }
    body += '<dl class="kv"><dt>Live now</dt><dd class="mono">' + p.live.commit + '</dd><dt>New version</dt><dd class="mono">' + p.beta.commit + '</dd>' +
      '<dt>Last snapshot</dt><dd>' + (snap ? A.ago(snap.at) : "none") + ' — a fresh one is taken first</dd></dl>' +
      A.otpHtml() + '<div class="msg msg-bad" id="gl-err" hidden></div><div data-run></div>';

    var dlg = A.dialog({
      title: "Go Live · " + p.name, body: body, wide: true,
      actions: [{ label: "Go Live", kind: "primary", id: "gl-go", onClick: function (d) {
        var over = d.querySelector("#override"), err = d.querySelector("#gl-err");
        var ship = approved.slice(), reason = "";
        err.hidden = true;
        if (over && over.checked) {
          reason = d.querySelector("#reason").value.trim();
          if (!reason) { err.textContent = "Write why these go live without client approval."; err.hidden = false; return false; }
          ship = ship.concat(unapproved);
        }
        if (!ship.length) { err.textContent = "Nothing to send. Wait for the client to approve, or tick the box above with a reason."; err.hidden = false; return false; }
        if (!A.otpOk(d)) return false;
        d.querySelector("#gl-go").disabled = true;
        var n = testsTotal();
        A.runSteps(d.querySelector("[data-run]"), ["Snapshot of the live database", "Final sync: live → beta", "Re-run " + n + " tests on fresh data", "Deploy to live", "Start 15-minute close watch"], function () {
          var s = A.S(), now = Date.now();
          s.backups.push({ id: A.nextId(), projectId: p.id, type: "snapshot", at: now - 4000, sizeMb: 40 + (p.id * 37) % 600, ok: true });
          s.deployments.push({ id: A.nextId(), projectId: p.id, env: "live", commit: p.beta.commit, at: now, by: user.id, note: ship.map(function (c) { return c.title; }).join(" · "), changeIds: ship.map(function (c) { return c.id; }), rolledBack: false });
          p.live.commit = p.beta.commit;
          p.beta.syncAt = now;
          ship.forEach(function (c) { c.status = "live"; c.liveAt = now; });
          A.save();
          A.log("Go Live", p.id, ship.length + (ship.length === 1 ? " change" : " changes") + " → live " + p.live.commit + (reason ? " · without approval: " + reason : ""));
          d.querySelector(".dlg-body").innerHTML = '<p class="reason reason-ok">Live. Watching the site closely for the next 15 minutes — you get an alert if anything breaks.</p>';
          d.querySelector("#gl-go").hidden = true;
          d.querySelector(".dlg-foot [data-close]").textContent = "Close";
          current = null; refreshTop(); render();
        });
        return false;
      } }]
    });
    var ov = dlg.querySelector("#override");
    if (ov) ov.addEventListener("change", function () { dlg.querySelector("#reason-box").hidden = !this.checked; });
  }

  /* ---------- Rollback ---------- */
  function rollback() {
    if (!owner) return;
    var ds = A.S().deployments.filter(function (d) { return d.projectId === p.id && d.env === "live" && !d.rolledBack; }).sort(function (a, b) { return b.at - a.at; });
    if (ds.length < 2) { A.toast("There is no earlier version to go back to."); return; }
    var cur = ds[0], prev = ds[1];
    var snap = A.S().backups.filter(function (b) { return b.projectId === p.id && b.type === "snapshot" && b.ok && b.at <= cur.at; }).sort(function (a, b) { return b.at - a.at; })[0];
    var body = '<dl class="kv"><dt>Live now</dt><dd><span class="mono">' + cur.commit + '</span> · ' + A.stamp(cur.at) + '</dd>' +
      '<dt>Go back to</dt><dd><span class="mono">' + prev.commit + '</span> · ' + A.stamp(prev.at) + ' (last good version)</dd></dl>' +
      (snap ? '<label class="check"><input type="checkbox" id="rb-db"> <span>Also restore the database from the snapshot of ' + A.stamp(snap.at) + '. Data entered after it is lost — leave this off unless the release broke the data.</span></label>' : '') +
      A.otpHtml() + '<div data-run></div>';
    A.dialog({
      title: "Rollback · " + p.name, body: body,
      actions: [{ label: "Roll back", kind: "danger", id: "rb-go", onClick: function (d) {
        if (!A.otpOk(d)) return false;
        var withDb = d.querySelector("#rb-db") && d.querySelector("#rb-db").checked;
        d.querySelector("#rb-go").disabled = true;
        A.runSteps(d.querySelector("[data-run]"), ["Deploy code " + prev.commit].concat(withDb ? ["Restore database snapshot"] : []).concat(["Health check on live"]), function () {
          var s = A.S();
          cur.rolledBack = true;
          /* Changes that went out in the rolled-back release go back to beta: debug there, then the normal flow again. */
          (cur.changeIds || []).forEach(function (cid) {
            var c = change(cid); if (!c) return;
            c.status = "beta"; c.liveAt = null;
            c.chat.push({ from: "claude", at: Date.now(), text: "This change was rolled back from live. Tell me what went wrong and I will fix it on beta; then it goes through tests and client approval again." });
          });
          s.deployments.push({ id: A.nextId(), projectId: p.id, env: "live", commit: prev.commit, at: Date.now(), by: user.id, note: "Rollback from " + cur.commit, rolledBack: false });
          p.live.commit = prev.commit;
          p.incident = "";
          A.save();
          A.log("Rollback", p.id, cur.commit + " → " + prev.commit + (withDb ? " + database snapshot" : ""));
          A.toast("Live is back on " + prev.commit + ".");
          d.close(); refreshTop(); render();
        });
        return false;
      } }]
    });
  }

  /* ---------- Sync + beta deploy ---------- */
  function sync() {
    var mm = { off: "stay off", test: "go only to test numbers", on: "stay ON for real users — check Settings" }[p.beta.msgMode];
    A.dialog({
      title: "Live → Beta sync",
      body: '<p>Copies the live database to beta, as it is right now. Beta\'s current data is replaced. Live is only read, never changed.</p>' +
        '<p class="hint">After the copy, beta messages ' + mm + ' and payments stay on ' + (p.beta.payMode === "sandbox" ? "sandbox" : "LIVE keys") + '.</p><div data-run></div>',
      actions: [{ label: "Sync now", kind: "primary", onClick: function (d) {
        d.querySelector("[data-act]").disabled = true;
        A.runSteps(d.querySelector("[data-run]"), ["Read-only copy of the live database", "Load it into beta", "Apply beta safety switches"], function () {
          p.beta.syncAt = Date.now(); A.save();
          A.log("Sync", p.id, "Live → Beta database copy");
          A.toast("Beta now has today's live data.");
          d.close(); renderEnvs();
        });
        return false;
      } }]
    });
  }
  function betaDeploy() {
    var n = testsTotal();
    A.dialog({
      title: "Beta deploy",
      body: '<p>Puts the latest beta code on the beta server and runs all tests.</p><div data-run></div>',
      actions: [{ label: "Deploy to beta", kind: "primary", onClick: function (d) {
        d.querySelector("[data-act]").disabled = true;
        A.runSteps(d.querySelector("[data-run]"), ["Pull the beta branch", "Run database changes on beta", "Run " + n + " tests"], function () {
          var s = A.S();
          p.beta.commit = A.hex(Date.now() % 100000);
          s.deployments.push({ id: A.nextId(), projectId: p.id, env: "beta", commit: p.beta.commit, at: Date.now(), by: user.id, note: n + "/" + n + " tests passed", rolledBack: false });
          A.save(); A.log("Beta deploy", p.id, p.beta.commit + " · " + n + "/" + n + " tests passed");
          A.toast("Beta deployed · " + n + "/" + n + " tests passed.");
          d.close(); renderEnvs(); if (tab === "deploys") renderDeploys();
        });
        return false;
      } }]
    });
  }

  $("act-sync").addEventListener("click", sync);
  $("act-deploy").addEventListener("click", betaDeploy);
  $("act-live").addEventListener("click", goLive);
  $("act-rollback").addEventListener("click", rollback);

  /* ---------- other tabs ---------- */
  function renderChanges() {
    var list = A.changesOf(p.id).sort(function (a, b) { return b.at - a.at; });
    $("panel").innerHTML = list.length ? '<div class="rows">' + list.map(function (c) {
      var si = statusInfo(c), h = { ok: 150, warn: 35, accent: 245 }[si[1]] || 220;
      return '<button class="row row-btn" type="button" data-open="' + c.id + '"><span class="row-lead"><span class="row-ic" style="--h:' + h + '">' + I.activity + '</span><span class="row-main"><span class="row-title">' + A.esc(c.title) + '</span>' +
        '<span class="row-sub">#' + c.id + ' · ' + A.esc(A.userName(c.by)) + ' · ' + A.ago(c.at) + '</span></span></span><span class="row-side">' + pill(c) + '</span></button>';
    }).join("") + '</div>' : '<div class="empty">No changes yet. Start one from the Chat tab.</div>';
  }
  $("panel").addEventListener("click", function (e) {
    var o = e.target.closest("[data-open]");
    if (o) { current = Number(o.getAttribute("data-open")); setTab("chat"); }
    var r = e.target.closest("[data-restore]");
    if (r) B.restore(r.getAttribute("data-restore"), function () { refreshTop(); renderBackups(); });
    if (e.target.closest("#backup-now")) B.backupNow(p.id, function () { refreshTop(); renderBackups(); });
  });

  function renderDeploys() {
    var list = A.S().deployments.filter(function (d) { return d.projectId === p.id; }).sort(function (a, b) { return b.at - a.at; });
    var liveNow = list.filter(function (d) { return d.env === "live" && !d.rolledBack; })[0];
    $("panel").innerHTML = '<div class="rows">' + list.map(function (d) {
      var side = d.rolledBack ? '<span class="pill pill-warn">Rolled back</span>' : d === liveNow ? '<span class="pill pill-ok">Live now</span>' : '';
      var ic = d.rolledBack ? ["undo", 24] : d.env === "live" ? ["rocket", 150] : ["upload", 215];
      return '<div class="row"><div class="row-lead"><span class="row-ic" style="--h:' + ic[1] + '">' + I[ic[0]] + '</span><div class="row-main"><span class="row-title">' + (d.env === "live" ? "Live" : "Beta") + ' · <span class="mono">' + d.commit + '</span></span>' +
        '<span class="row-sub">' + A.esc(d.note) + ' · ' + A.esc(A.userName(d.by)) + ' · ' + A.stamp(d.at) + '</span></div></div><div class="row-side">' + side + '</div></div>';
    }).join("") + '</div>';
  }

  function renderBackups() {
    var list = A.S().backups.filter(function (b) { return b.projectId === p.id; }).sort(function (a, b) { return b.at - a.at; }).slice(0, 24);
    $("panel").innerHTML = '<div class="section-head"><p class="muted">Daily database + files to cloud storage, and a snapshot before every Go Live.</p>' +
      '<button class="btn btn-sm btn-primary" type="button" id="backup-now">' + I.db + 'Back up now</button></div><div class="rows" id="b-list"></div>';
    B.renderList($("b-list"), list);
  }

  /* F13: message switch + payment test mode for beta */
  function renderSettings() {
    var b = p.beta;
    function radio(name, val, title, sub, cur, disabled) {
      return '<label><input type="radio" name="' + name + '" value="' + val + '"' + (cur === val ? " checked" : "") + (disabled ? " disabled" : "") + '><div><strong>' + title + '</strong><span>' + sub + '</span></div></label>';
    }
    var devs = A.S().users.filter(function (u) { return u.role === "developer" && u.projects !== "all" && u.projects.indexOf(p.id) !== -1; });
    $("panel").innerHTML = '<form class="set-grid" id="set-form">' +
      '<section class="panel-box"><h2><span class="h-ic" style="--h:199">' + I.send + '</span>Messages from beta</h2><div class="seg" role="radiogroup" aria-label="Messages from beta">' +
        radio("msg", "off", "Off", "No SMS, WhatsApp, email or push leaves beta. Default.", b.msgMode) +
        radio("msg", "test", "Test numbers only", "Every message goes only to the numbers below, never to real users.", b.msgMode) +
        radio("msg", "on", "On — real users", owner ? "Beta has real live data. Real customers will get these messages." : "Only the owner can turn this on.", b.msgMode, !owner) +
      '</div><div class="field" id="tn-box"' + (b.msgMode === "test" ? "" : " hidden") + '><label for="tn">Test numbers / emails (one per line)</label><textarea id="tn">' + A.esc(b.testNumbers) + '</textarea></div></section>' +
      '<section class="panel-box"><h2><span class="h-ic" style="--h:160">' + I.db + '</span>Payments on beta</h2><div class="seg" role="radiogroup" aria-label="Payments on beta">' +
        radio("pay", "sandbox", "Sandbox / test keys", "No real money ever moves on beta. Default.", b.payMode) +
        radio("pay", "live", "Live keys", owner ? "Real money. Use only for a short, planned check." : "Only the owner can turn this on.", b.payMode, !owner) +
      '</div></section>' +
      '<section class="panel-box"><h2><span class="h-ic" style="--h:' + A.hue(p) + '">' + I.github + '</span>Project</h2><dl class="kv">' +
        '<dt>GitHub</dt><dd class="mono">' + A.esc(p.repo) + '</dd><dt>Hosting</dt><dd>' + p.host + ' · <span class="mono">' + A.esc(p.server) + '</span></dd>' +
        '<dt>Beta</dt><dd class="mono">' + A.esc(p.beta.url) + '</dd><dt>Live</dt><dd class="mono">' + A.esc(p.live.url) + '</dd>' +
        '<dt>Developers</dt><dd>' + (devs.length ? devs.map(function (u) { return A.esc(u.name) + (u.active ? "" : " (off)"); }).join(", ") : "None") + '</dd></dl></section>' +
      '<div><button class="btn btn-primary" type="submit">Save settings</button></div></form>';
    var f = $("set-form");
    f.addEventListener("change", function (e) {
      if (e.target.name === "msg") $("tn-box").hidden = e.target.value !== "test";
    });
    f.addEventListener("submit", function (e) {
      e.preventDefault();
      var m = f.querySelector('input[name="msg"]:checked').value, pay = f.querySelector('input[name="pay"]:checked').value;
      b.msgMode = m; b.payMode = pay; b.testNumbers = $("tn").value.trim();
      A.save(); A.log("Settings", p.id, "Beta messages: " + m + " · payments: " + pay);
      A.toast("Saved."); renderEnvs();
    });
  }

  /* ---------- start ---------- */
  renderHead();
  renderEnvs();
  var first = openChanges()[0];
  current = first ? first.id : null;
  var h = (window.location.hash || "").replace("#", "");
  setTab(["chat", "changes", "deploys", "backups", "settings"].indexOf(h) !== -1 ? h : "chat");
});

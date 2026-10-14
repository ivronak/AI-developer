/* Shared backup pieces (S7): list rows, "Back up now", Restore. Used by backups.html and the project page's Backups tab. */
(function () {
  var A = window.APP;
  var TYPE = { db: "Database backup", files: "Uploaded files", snapshot: "Snapshot before Go Live" };

  function sizeText(mb) { return mb >= 1024 ? (mb / 1024).toFixed(1) + " GB" : mb + " MB"; }
  function dayKey(ts) { var d = new Date(ts); return d.getFullYear() + "-" + d.getMonth() + "-" + d.getDate(); }
  function dayLabel(ts) {
    var d = new Date(ts), t = new Date();
    if (dayKey(ts) === dayKey(t.getTime())) return "Today";
    t.setDate(t.getDate() - 1);
    if (dayKey(ts) === dayKey(t.getTime())) return "Yesterday";
    return A.stamp(ts).split(",")[0];
  }

  /* list: backups, newest first. opts.showProject adds the project name. */
  function renderList(el, list, opts) {
    opts = opts || {};
    if (!list.length) { el.innerHTML = '<div class="empty">No backups match.</div>'; return; }
    var html = "", lastDay = "";
    list.forEach(function (b) {
      var k = dayKey(b.at);
      if (k !== lastDay) { html += '<div class="day-head">' + dayLabel(b.at) + '</div>'; lastDay = k; }
      var p = A.project(b.projectId);
      var ic = !b.ok ? ["x", 0] : b.type === "db" ? ["db", 175] : b.type === "files" ? ["upload", 199] : ["rocket", 265];
      html += '<div class="row' + (b.ok ? "" : " row-fail") + '">' +
        '<div class="row-lead"><span class="row-ic" style="--h:' + ic[1] + '">' + A.ICONS[ic[0]] + '</span>' +
        '<div class="row-main"><span class="row-title">' + (opts.showProject ? '<span class="pav xs" style="--h:' + A.hue(p) + '">' + A.initials(p.name) + '</span> ' + A.esc(p.name) + ' · ' : '') + TYPE[b.type] + '</span>' +
        '<span class="row-sub">' + (b.ok ? sizeText(b.sizeMb) + ' · ' : '') + A.stamp(b.at) + ' · ' + (b.ok ? "Cloud storage" : A.esc(b.error || "Failed")) + '</span></div></div>' +
        '<div class="row-side">' + (b.ok
          ? '<span class="pill pill-ok">Saved</span>' + (b.type !== "files" ? '<button class="btn btn-sm" type="button" data-restore="' + b.id + '">Restore</button>' : '')
          : '<span class="pill pill-bad">Failed</span>') +
        '</div></div>';
    });
    el.innerHTML = html;
  }

  function backupNow(projectId, done) {
    var p = A.project(projectId);
    var dlg = A.dialog({
      title: "Back up now",
      body: '<p>Back up <strong>' + A.esc(p.name) + '</strong> now: the database and the uploaded files go to cloud storage, outside the server.</p><div data-run></div>',
      actions: [{ label: "Start backup", kind: "primary", onClick: function (d) {
        d.querySelector("[data-act]").disabled = true;
        A.runSteps(d.querySelector("[data-run]"), ["Dump the database", "Copy uploaded files", "Upload to cloud storage", "Check the backup opens"], function () {
          var s = A.S(), now = Date.now();
          s.backups.push({ id: A.nextId(), projectId: p.id, type: "db", at: now, sizeMb: 40 + (p.id * 37) % 600, ok: true });
          s.backups.push({ id: A.nextId(), projectId: p.id, type: "files", at: now + 1000, sizeMb: 300 + (p.id * 113) % 2400, ok: true });
          A.save();
          A.log("Backup", p.id, "Manual backup: database + files");
          A.toast(p.name + ": backup saved.");
          d.close();
          if (done) done();
        });
        return false;
      } }]
    });
    return dlg;
  }

  function restore(backupId, done) {
    var b = A.S().backups.filter(function (x) { return x.id === Number(backupId); })[0];
    if (!b) return;
    var p = A.project(b.projectId), owner = A.isOwner(A.currentUser());
    var body =
      '<dl class="kv"><dt>Project</dt><dd>' + A.esc(p.name) + '</dd><dt>Backup</dt><dd>' + TYPE[b.type] + ' · ' + A.stamp(b.at) + '</dd></dl>' +
      '<div class="seg" role="radiogroup" aria-label="Restore to">' +
        '<label><input type="radio" name="rt" value="beta" checked><div><strong>Restore to beta</strong><span>Safe. Use it to check the backup or look at old data.</span></div></label>' +
        '<label><input type="radio" name="rt" value="live"' + (owner ? '' : ' disabled') + '><div><strong>Restore to live</strong><span>' +
          (owner ? 'Live goes back to this point. Anything entered after ' + A.stamp(b.at) + ' is lost. A snapshot of live is taken first.' : 'Only the owner can restore live.') +
        '</span></div></label>' +
      '</div>' +
      '<div data-otp hidden>' + A.otpHtml() + '</div><div data-run></div>';
    var dlg = A.dialog({
      title: "Restore backup", body: body,
      actions: [{ label: "Restore", kind: "primary", id: "restore-go", onClick: function (d) {
        var target = d.querySelector('input[name="rt"]:checked').value;
        if (target === "live" && !A.otpOk(d)) return false;
        d.querySelector("#restore-go").disabled = true;
        var steps = target === "live"
          ? ["Snapshot live as it is now", "Restore " + TYPE[b.type].toLowerCase() + " to live", "Health check on live"]
          : ["Restore " + TYPE[b.type].toLowerCase() + " to beta", "Turn beta messages off", "Health check on beta"];
        A.runSteps(d.querySelector("[data-run]"), steps, function () {
          var s = A.S();
          if (target === "live") {
            s.backups.push({ id: A.nextId(), projectId: p.id, type: "snapshot", at: Date.now(), sizeMb: b.sizeMb, ok: true });
            p.incident = "";
          } else {
            p.beta.msgMode = "off";
          }
          A.save();
          A.log("Restore", p.id, TYPE[b.type] + " from " + A.stamp(b.at) + " → " + target);
          A.toast(p.name + ": restored to " + target + ".");
          d.close();
          if (done) done();
        });
        return false;
      } }]
    });
    var radios = dlg.querySelectorAll('input[name="rt"]');
    for (var i = 0; i < radios.length; i++) {
      radios[i].addEventListener("change", function () {
        dlg.querySelector("[data-otp]").hidden = this.value !== "live" || !this.checked;
        dlg.querySelector("#restore-go").className = "btn " + (this.value === "live" ? "btn-danger" : "btn-primary");
      });
    }
  }

  window.BACKUPS = { renderList: renderList, backupNow: backupNow, restore: restore, TYPE: TYPE };
})();

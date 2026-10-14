/* S7 Backups: date-wise list across projects, filters, Back up now, Restore (live needs OTP + owner). */
document.addEventListener("DOMContentLoaded", function () {
  if (!window.SHELL || window.SHELL.blocked) return;
  var A = window.APP, B = window.BACKUPS, D = window.DEMO, user = window.SHELL.user;
  var $ = function (id) { return document.getElementById(id); };
  var LIMIT = 60;
  var mine = A.myProjects(user);

  $("backup-now").insertAdjacentHTML("afterbegin", A.ICONS.db);
  $("f-project").insertAdjacentHTML("beforeend", mine.map(function (p) {
    return '<option value="' + p.id + '">' + A.esc(p.name) + '</option>';
  }).join(""));
  var pre = A.qs("project");
  if (pre) $("f-project").value = pre;

  function summary() {
    var dayAgo = Date.now() - 86400000;
    var recent = A.S().backups.filter(function (b) { return b.at > dayAgo && b.type !== "snapshot" && A.canSee(user, b.projectId); });
    var failed = recent.filter(function (b) { return !b.ok; });
    var stale = mine.filter(function (p) { var lb = A.lastBackup(p.id); return !lb || A.minSince(lb.at) > D.BACKUP_STALE_MIN; });
    $("head-sub").textContent = "Daily database + files backups and a snapshot before every Go Live — stored in cloud storage, outside the servers.";
    var snaps = A.S().backups.filter(function (b) { return b.type === "snapshot" && b.at > dayAgo - 6 * 86400000 && A.canSee(user, b.projectId); }).length;
    function k(h, icon, val, label, cls) {
      return '<div class="kpi static' + (cls ? " " + cls : "") + '" style="--h:' + h + '"><span class="kpi-ic">' + A.ICONS[icon] + '</span><b>' + val + '</b><span class="l">' + label + '</span></div>';
    }
    $("b-kpis").innerHTML =
      k(160, "check", recent.filter(function (b) { return b.ok; }).length, "Saved in last 24 h") +
      k(0, "x", failed.length, "Failed in last 24 h", failed.length ? "alarm" : "") +
      k(35, "db", stale.length, "Projects with an old backup", stale.length ? "alarm" : "") +
      k(265, "rocket", snaps, "Go Live snapshots (7 days)");
    if (stale.length) {
      $("fail-strip").innerHTML = '<span>No good backup for over a day: ' + stale.map(function (p) { return A.esc(p.name); }).join(", ") + '</span>';
      $("fail-strip").hidden = false;
    } else {
      $("fail-strip").hidden = true;
    }
  }

  function render() {
    var pid = Number($("f-project").value) || 0, type = $("f-type").value, failedOnly = $("f-status").value === "failed";
    var list = A.S().backups.filter(function (b) {
      return A.canSee(user, b.projectId) && (!pid || b.projectId === pid) && (!type || b.type === type) && (!failedOnly || !b.ok);
    }).sort(function (a, b) { return b.at - a.at; });
    B.renderList($("list"), list.slice(0, LIMIT), { showProject: !pid });
    $("more-note").hidden = list.length <= LIMIT;
    $("more-note").textContent = "Showing the newest " + LIMIT + " of " + list.length + ". Pick a project to narrow it down.";
    summary();
  }

  ["f-project", "f-type", "f-status"].forEach(function (id) { $(id).addEventListener("change", render); });
  $("list").addEventListener("click", function (e) {
    var r = e.target.closest("[data-restore]");
    if (r) B.restore(r.getAttribute("data-restore"), render);
  });
  $("backup-now").addEventListener("click", function () {
    var pid = Number($("f-project").value);
    if (pid) { B.backupNow(pid, render); return; }
    A.dialog({
      title: "Back up now",
      body: '<div class="field"><label for="pick-p">Which project?</label><select id="pick-p">' + mine.map(function (p) {
        return '<option value="' + p.id + '">' + A.esc(p.name) + '</option>';
      }).join("") + '</select></div>',
      actions: [{ label: "Next", kind: "primary", onClick: function (d) {
        var v = Number(d.querySelector("#pick-p").value);
        setTimeout(function () { B.backupNow(v, render); }, 0);
      } }]
    });
  });

  render();
});

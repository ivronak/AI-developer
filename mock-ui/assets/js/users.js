/* S8 Users & log (owner only): add / edit users, roles, project assignment, one-tap access off, activity log with filters. */
document.addEventListener("DOMContentLoaded", function () {
  if (!window.SHELL || window.SHELL.blocked) return;
  var A = window.APP, me = window.SHELL.user;
  var $ = function (id) { return document.getElementById(id); };
  var LOG_LIMIT = 80;

  $("add-user").insertAdjacentHTML("afterbegin", A.ICONS.plus);

  function projNames(u) {
    if (u.projects === "all") return "All projects";
    if (!u.projects.length) return "No projects";
    return u.projects.map(function (id) { var p = A.project(id); return p ? p.name : ""; }).join(", ");
  }

  function renderUsers() {
    $("users").innerHTML = A.S().users.map(function (u) {
      var isMe = u.id === me.id;
      var h = u.role === "owner" ? 245 : A.hue({ id: u.id + 4 });
      var chips = u.projects === "all" ? '<span class="tech" style="--h:245">All projects</span>'
        : u.projects.length ? u.projects.map(function (id) { var pp = A.project(id); return pp ? '<span class="tech" style="--h:' + A.hue(pp) + '">' + A.esc(pp.name) + '</span>' : ""; }).join("")
        : '<span class="tag-off">No projects</span>';
      return '<article class="ucard' + (u.active ? "" : " is-off") + '" style="--h:' + h + '">' +
        '<div class="ucard-band"><span class="pav' + (u.active ? "" : " off") + '">' + A.initials(u.name) + '</span><div class="ucard-id"><h2>' + A.esc(u.name) + (isMe ? ' <span class="hint">(you)</span>' : '') + '</h2>' +
          '<p class="contact">' + A.esc(u.email) + ' · ' + A.esc(u.mobile) + '</p></div>' +
          '<span class="hpill ' + (u.active ? "s-ok" : "s-bad") + '">' + (u.active ? "Access on" : "Access off") + '</span></div>' +
        '<div class="ucard-body"><div class="chip-row"><span class="role-chip">' + (u.role === "owner" ? "Owner" : "Developer") + '</span>' + chips + '</div>' +
        '<dl class="kv"><dt>Last sign-in</dt><dd>' + A.ago(u.lastAt) + '</dd></dl>' +
        (isMe ? '' : '<div class="ucard-acts">' +
          '<button class="btn btn-sm" type="button" data-edit="' + u.id + '">Edit</button>' +
          (u.active
            ? '<button class="btn btn-sm btn-danger" type="button" data-off="' + u.id + '">Turn access off</button>'
            : '<button class="btn btn-sm" type="button" data-on="' + u.id + '">Turn access on</button>') +
        '</div>') +
      '</div></article>';
    }).join("");
  }

  function editUser(u) {
    var isNew = !u;
    u = u || { name: "", email: "", mobile: "", role: "developer", projects: [] };
    var ticks = A.S().projects.map(function (p) {
      var on = u.projects === "all" || u.projects.indexOf(p.id) !== -1;
      return '<label class="check"><input type="checkbox" name="proj" value="' + p.id + '"' + (on ? " checked" : "") + '> <span>' + A.esc(p.name) + '</span></label>';
    }).join("");
    A.dialog({
      title: isNew ? "Add user" : "Edit " + u.name, wide: true,
      body:
        '<div class="field"><label for="u-name">Name</label><input id="u-name" value="' + A.esc(u.name) + '"></div>' +
        '<div class="field"><label for="u-email">Email</label><input id="u-email" type="email" value="' + A.esc(u.email) + '"></div>' +
        '<div class="field"><label for="u-mobile">Mobile (for OTP)</label><input id="u-mobile" inputmode="tel" value="' + A.esc(u.mobile) + '"></div>' +
        '<div class="field"><label for="u-role">Role</label><select id="u-role"><option value="developer">Developer — only the projects ticked below</option></select>' +
          '<span class="hint">There is one owner. Clients never get a login — they use the approval link.</span></div>' +
        '<div class="field"><span class="label">Projects</span><div class="proj-ticks">' + ticks + '</div></div>' +
        (isNew ? '<p class="hint">They set their own password from a sign-up link. Nobody else ever knows it.</p>' : '') +
        '<div class="msg msg-bad" id="u-err" hidden></div>',
      actions: [{ label: isNew ? "Add user" : "Save", kind: "primary", onClick: function (d) {
        var name = d.querySelector("#u-name").value.trim(), email = d.querySelector("#u-email").value.trim().toLowerCase();
        var mobile = d.querySelector("#u-mobile").value.trim(), err = d.querySelector("#u-err");
        var projs = [].map.call(d.querySelectorAll('input[name="proj"]:checked'), function (x) { return Number(x.value); });
        var taken = A.S().users.some(function (x) { return x.email === email && x !== u; });
        var msg = !name ? "Enter a name." : !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email) ? "Enter a valid email." : taken ? "Someone already uses this email."
          : mobile.replace(/\D/g, "").length < 10 ? "Enter a mobile number with at least 10 digits." : "";
        if (msg) { err.textContent = msg; err.hidden = false; return false; }
        if (isNew) {
          u = { id: A.nextId(), name: name, email: email, mobile: mobile, role: "developer", projects: projs, active: true, lastAt: Date.now() };
          A.S().users.push(u);
          A.log("User added", 0, name + " · " + projs.length + (projs.length === 1 ? " project" : " projects"));
        } else {
          u.name = name; u.email = email; u.mobile = mobile; u.projects = projs;
          A.log("User changed", 0, name + " · " + projs.length + (projs.length === 1 ? " project" : " projects"));
        }
        A.save(); A.toast(isNew ? "User added." : "Saved."); renderUsers(); renderLog();
      } }]
    });
  }

  $("add-user").addEventListener("click", function () { editUser(null); });
  $("users").addEventListener("click", function (e) {
    var ed = e.target.closest("[data-edit]"), off = e.target.closest("[data-off]"), on = e.target.closest("[data-on]");
    if (ed) editUser(A.user(Number(ed.getAttribute("data-edit"))));
    if (off) {
      var u = A.user(Number(off.getAttribute("data-off")));
      A.dialog({
        title: "Turn access off",
        body: '<p><strong>' + A.esc(u.name) + '</strong> is signed out everywhere right now and can no longer sign in or see any project. Their history stays in the log.</p>',
        actions: [{ label: "Turn access off", kind: "danger", onClick: function () {
          u.active = false; A.save(); A.log("Access off", 0, u.name + " — access turned off");
          A.toast(u.name + ": access off."); renderUsers(); renderLog();
        } }]
      });
    }
    if (on) {
      var u2 = A.user(Number(on.getAttribute("data-on")));
      u2.active = true; A.save(); A.log("Access on", 0, u2.name + " — access turned on");
      A.toast(u2.name + ": access on."); renderUsers(); renderLog();
    }
  });

  /* ---------- activity log ---------- */
  $("l-user").insertAdjacentHTML("beforeend", '<option value="0">System / client</option>' + A.S().users.map(function (u) {
    return '<option value="' + u.id + '">' + A.esc(u.name) + '</option>';
  }).join(""));
  $("l-project").insertAdjacentHTML("beforeend", A.S().projects.map(function (p) {
    return '<option value="' + p.id + '">' + A.esc(p.name) + '</option>';
  }).join(""));

  function dayStart(v) { var d = new Date(v + "T00:00:00"); return d.getTime(); }
  function renderLog() {
    var uid = $("l-user").value, pid = Number($("l-project").value) || 0;
    var from = $("l-from").value ? dayStart($("l-from").value) : 0;
    var to = $("l-to").value ? dayStart($("l-to").value) + 86400000 : Infinity;
    var list = A.S().log.filter(function (l) {
      return (uid === "" || l.userId === Number(uid)) && (!pid || l.projectId === pid) && l.at >= from && l.at < to;
    }).sort(function (a, b) { return b.at - a.at; });
    $("log-count").textContent = list.length + (list.length === 1 ? " entry" : " entries");
    var ICON = {
      "Go Live": ["rocket", 150], "Rollback": ["undo", 24], "Client approved": ["check", 150], "Client asked changes": ["activity", 35],
      "Approved for client": ["check", 150], "Changes for client": ["x", 35], "Sent to client": ["send", 215], "Prompt": ["activity", 265],
      "Backup": ["db", 175], "Restore": ["db", 24], "Sync": ["sync", 199], "Beta deploy": ["upload", 220], "Alert": ["bell", 0],
      "Tests": ["check", 160], "Settings": ["activity", 240], "Sign in": ["users", 245], "Sign out": ["logout", 245],
      "User added": ["plus", 245], "User changed": ["users", 245], "Access off": ["x", 0], "Access on": ["check", 150]
    };
    var html = "", lastDay = "";
    list.slice(0, LOG_LIMIT).forEach(function (l) {
      var day = A.stamp(l.at).split(",")[0];
      if (day !== lastDay) { html += '<div class="day-head">' + day + '</div>'; lastDay = day; }
      var p = l.projectId ? A.project(l.projectId) : null;
      var who = l.userId ? A.userName(l.userId) : (l.action.indexOf("Client") === 0 ? "Client" : "System");
      var ic = ICON[l.action] || ["activity", 240];
      html += '<div class="row"><div class="row-lead"><span class="row-ic" style="--h:' + ic[1] + '">' + A.ICONS[ic[0]] + '</span><div class="row-main"><span class="row-title">' + A.esc(l.action) +
        (p ? ' · <span class="pav xs" style="--h:' + A.hue(p) + '">' + A.initials(p.name) + '</span> ' + A.esc(p.name) : '') + '</span>' +
        '<span class="row-sub">' + A.esc(l.details) + '</span>' +
        (l.prompt ? '<span class="log-prompt">Prompt: ' + A.esc(l.prompt) + '</span>' : '') +
        '</div></div><div class="row-side"><span class="row-sub">' + A.esc(who) + '<br>' + A.stamp(l.at).split(", ")[1] + '</span></div></div>';
    });
    $("log").innerHTML = html || '<div class="empty">No activity matches.</div>';
  }
  ["l-user", "l-project", "l-from", "l-to"].forEach(function (id) { $(id).addEventListener("change", renderLog); });

  renderUsers();
  renderLog();
});

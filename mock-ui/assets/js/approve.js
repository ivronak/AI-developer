/* S5 Client approval: one-time link, no login. Closes after 7 days or after the client replies. Every reply is logged. */
document.addEventListener("DOMContentLoaded", function () {
  var A = window.APP, D = window.DEMO;
  var $ = function (id) { return document.getElementById(id); };
  var tok = A.qs("t") || "";
  var c = A.S().changes.filter(function (x) { return x.approval && x.approval.token === tok; })[0];

  function done(title, cls, text) {
    $("content").innerHTML = '<div class="ap-card ap-done"><p class="big ' + (cls || "") + '">' + title + '</p><p class="muted">' + text + '</p></div>';
  }

  if (!c) { done("This link is not valid", "", "Ask the team for a new link."); return; }
  var a = c.approval, p = A.project(c.projectId);
  if (a.decision) {
    done(a.decision === "approved" ? "Already approved" : "Already answered", a.decision === "approved" ? "ok" : "warn",
      "You replied on " + A.stamp(a.decidedAt) + ". This link is now closed.");
    return;
  }
  if (A.minSince(a.sentAt) > D.LINK_DAYS * 1440) { done("This link has expired", "", "Links stay open for " + D.LINK_DAYS + " days. Ask the team for a new one."); return; }

  var ask = c.chat.filter(function (m) { return m.from === "owner"; })[0];
  $("content").innerHTML =
    '<section class="hero" style="--h:' + A.hue(p) + '"><span class="pav">' + A.initials(p.name) + '</span>' +
      '<div class="hero-id"><p class="ap-for">' + A.esc(p.name) + ' · for ' + A.esc(p.client) + '</p><h1>' + A.esc(c.title) + '</h1></div></section>' +
    '<section class="ap-card">' +
      (ask ? '<p class="ap-desc">' + A.esc(ask.text) + '</p>' : '') +
      '<div><div class="label">Please check</div><ul class="ap-list">' + a.checklist.map(function (x) { return '<li>' + A.esc(x) + '</li>'; }).join("") + '</ul></div>' +
      '<a class="btn btn-block" href="' + p.beta.url + '" target="_blank" rel="noopener">' + A.ICONS.ext + 'Open the test site</a>' +
      '<p class="hint">The test site has a copy of your real data. Nothing you do there reaches your customers.</p>' +
    '</section>' +
    '<section class="ap-card" id="answer">' +
      '<div class="ap-acts">' +
        '<button class="btn btn-ok" type="button" id="yes">' + A.ICONS.check + 'Approve</button>' +
        '<button class="btn" type="button" id="no">Changes needed</button>' +
      '</div>' +
      '<form id="no-form" hidden novalidate>' +
        '<div class="field"><label for="comment">What should change?</label><textarea id="comment" placeholder="e.g. The search box should also find old entries"></textarea></div>' +
        '<div class="field"><label for="shot">Screenshot (optional)</label><input id="shot" type="file" accept="image/*"></div>' +
        '<div class="msg msg-bad" id="no-err" hidden>Please write what should change.</div>' +
        '<button class="btn btn-primary btn-block" type="submit">Send</button>' +
      '</form>' +
    '</section>' +
    '<p class="ap-note">Link open until ' + A.stamp(a.sentAt + D.LINK_DAYS * 86400000) + '. Mock page — in the real system this link comes from the dashboard server.</p>';

  function closeWith(decision, comment, shot) {
    var now = Date.now();
    a.decision = decision; a.decidedAt = now; a.comment = comment || "";
    if (decision === "approved") {
      c.status = "approved";
      A.alert(p.id, p.name + ": client approved a change");
    } else {
      c.status = "changes";
      c.chat.push({ from: "client", at: now, text: comment, shot: shot || null });
      A.alert(p.id, p.name + ": client asked for changes");
    }
    A.S().log.unshift({ at: now, userId: 0, projectId: p.id, action: decision === "approved" ? "Client approved" : "Client asked changes", details: c.title + (comment ? " — " + comment : ""), prompt: "" });
    A.save();
  }

  $("yes").addEventListener("click", function () {
    closeWith("approved");
    done("Thank you — approved", "ok", "The team will put this change live. This link is now closed.");
  });
  $("no").addEventListener("click", function () { $("no-form").hidden = false; $("comment").focus(); });
  $("no-form").addEventListener("submit", function (e) {
    e.preventDefault();
    var t = $("comment").value.trim();
    if (!t) { $("no-err").hidden = false; return; }
    var f = $("shot").files && $("shot").files[0];
    closeWith("changes", t, f ? f.name : null);
    done("Thanks — sent to the team", "warn", "They will make the change and send you a new link.");
  });
});

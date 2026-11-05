/* Project page behaviour (data is rendered by the server): tabs (kept in the #hash), change picker, settings form,
   "coming in step N" notes. */
document.addEventListener("DOMContentLoaded", function () {
  var TABS = ["chat", "changes", "apps", "deploys", "backups", "settings"];

  function setTab(t) {
    if (TABS.indexOf(t) === -1) t = "chat";
    var b = document.querySelectorAll("[data-tab]");
    for (var i = 0; i < b.length; i++) b[i].setAttribute("aria-selected", String(b[i].getAttribute("data-tab") === t));
    var p = document.querySelectorAll("[data-panel]");
    for (var j = 0; j < p.length; j++) p[j].hidden = p[j].getAttribute("data-panel") !== t;
    try { history.replaceState(null, "", window.location.pathname + window.location.search + "#" + t); } catch (e) {}
  }

  function setHint() {
    var box = document.getElementById("msg"), dbgBox = document.getElementById("cc-debug"), ch = document.getElementById("composer-change");
    if (!box) return;
    box.placeholder = dbgBox && dbgBox.checked ? "Debug: where does it happen (page or URL)? What goes wrong? What did you expect? Any error text?"
      : (ch && ch.value ? "Tell Claude what to adjust…" : "e.g. Client wants the logo bigger on the login page");
  }

  function pick(id) {
    var b = document.querySelectorAll("[data-pick]"), lock = "", dbg = "";
    for (var i = 0; i < b.length; i++) {
      var on = b[i].getAttribute("data-pick") === id;
      b[i].setAttribute("aria-pressed", String(on));
      if (on) { lock = b[i].getAttribute("data-lock") || ""; dbg = b[i].getAttribute("data-debug") || ""; }
    }
    var s = document.querySelectorAll("[data-change]");
    for (var j = 0; j < s.length; j++) s[j].hidden = s[j].getAttribute("data-change") !== id;
    /* the composer follows the picked change: follow-up message, or a brand-new change */
    var form = document.getElementById("composer");
    if (!form) return;
    document.getElementById("composer-change").value = id === "new" ? "" : id;
    document.getElementById("send-label").textContent = id === "new" ? "Start change" : "Send";
    /* Debug mode follows the picked change: a follow-up on a debug change stays a debug run unless the box is unticked */
    var dbgBox = document.getElementById("cc-debug");
    if (dbgBox && dbgBox.checked !== (dbg === "1")) { dbgBox.checked = dbg === "1"; dbgBox.dispatchEvent(new Event("change")); }
    setHint();
    form.classList.toggle("is-locked", !!lock);
    var note = document.getElementById("composer-locked");
    note.hidden = !lock; note.textContent = lock;
    document.getElementById("composer-send").disabled = !!lock;
  }

  document.addEventListener("click", function (e) {
    var t = e.target.closest("[data-tab]");
    if (t) { setTab(t.getAttribute("data-tab")); return; }
    var p = e.target.closest("[data-pick]");
    if (p) { pick(p.getAttribute("data-pick")); return; }
    var cp = e.target.closest("[data-copy-v]");
    if (cp) {
      var v = cp.getAttribute("data-copy-v");
      if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(v).then(function () { UI.toast("Link copied."); });
      else { var ta = document.createElement("textarea"); ta.value = v; document.body.appendChild(ta); ta.select();
             try { document.execCommand("copy"); UI.toast("Link copied."); } catch (x) {} document.body.removeChild(ta); }
      return;
    }
    var more = e.target.closest("[data-more]");
    if (more) {
      var md = more.previousElementSibling;
      var open = md.classList.toggle("open");
      more.textContent = open ? "Show less" : "Show all";
      return;
    }
    var s = e.target.closest("[data-soon]");
    if (s) UI.toast(s.getAttribute("data-soon"));
  });

  document.addEventListener("change", function (e) {
    if (e.target.id === "gl-override") {
      var rb = document.getElementById("gl-reason-box");
      rb.hidden = !e.target.checked;
      document.getElementById("gl-reason").required = e.target.checked;
    }
    if (e.target.name === "msg") {
      var box = document.getElementById("tn-box");
      if (box) box.hidden = e.target.value !== "test";
    }
  });

  /* Screenshots: the Screenshot button, Ctrl+V of a copied image, or dragging image files onto the page. They wait as small previews
     (a × takes one away) until Send; the hidden file input is what the form really sends. */
  var shots = document.getElementById("shots"), composerForm = document.getElementById("composer");
  if (shots && composerForm && window.DataTransfer && window.File) {
    var maxImages = parseInt(composerForm.getAttribute("data-max-images"), 10) || 3;
    var maxMb = parseInt(composerForm.getAttribute("data-max-mb"), 10) || 5;
    var okTypes = { "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif" };
    var pending = [], shotUrls = [], pastedN = 0, dragDepth = 0;
    var previews = document.getElementById("shot-previews"), shotHint = document.getElementById("shot-names"), dropHint = document.getElementById("drop-hint");
    var say = function (t) { if (window.UI && UI.toast) UI.toast(t); };
    var shotLocked = function () { return composerForm.classList.contains("is-locked"); };
    var lockText = function () { var n = document.getElementById("composer-locked"); return (n && n.textContent) || "Claude is working on this change…"; };
    var composerVisible = function () { return composerForm.offsetParent !== null; };

    var renderShots = function () {
      var dt = new DataTransfer(), i;
      for (i = 0; i < pending.length; i++) dt.items.add(pending[i]);
      shots.files = dt.files;
      for (i = 0; i < shotUrls.length; i++) URL.revokeObjectURL(shotUrls[i]);
      shotUrls = [];
      previews.textContent = "";
      for (i = 0; i < pending.length; i++) {
        var li = document.createElement("li"), img = document.createElement("img"), cap = document.createElement("span"), x = document.createElement("button");
        var url = URL.createObjectURL(pending[i]);
        shotUrls.push(url);
        img.src = url; img.alt = "";
        cap.className = "shot-cap"; cap.textContent = pending[i].name; cap.title = pending[i].name + " · " + Math.max(1, Math.round(pending[i].size / 1024)) + " KB";
        x.type = "button"; x.className = "shot-x"; x.textContent = "×";
        x.setAttribute("data-remove", String(i)); x.setAttribute("aria-label", "Remove " + pending[i].name);
        li.appendChild(img); li.appendChild(cap); li.appendChild(x); previews.appendChild(li);
      }
      previews.hidden = !pending.length;
      shotHint.textContent = pending.length ? pending.length + " of " + maxImages + " screenshots · paste or drop more" : "or paste / drop images";
    };

    var addShots = function (list) {
      var problem = "", added = 0;
      for (var i = 0; i < list.length; i++) {
        var f = list[i], pasted = !f.name || /^image\.\w+$/i.test(f.name);
        if (!okTypes[f.type]) { problem = "Only PNG, JPG, WEBP or GIF images can be attached."; continue; }
        if (f.size > maxMb * 1048576) { problem = (f.name || "That image") + " is bigger than " + maxMb + " MB."; continue; }
        if (pending.length >= maxImages) { problem = "Up to " + maxImages + " screenshots at a time."; continue; }
        if (!pasted) {   /* the same file picked or dropped twice is only attached once */
          var dup = false;
          for (var j = 0; j < pending.length; j++) if (pending[j].name === f.name && pending[j].size === f.size && pending[j].lastModified === f.lastModified) dup = true;
          if (dup) continue;
        } else {         /* a pasted image is always called "image.png": give each its own name */
          f = new File([f], "pasted-" + (++pastedN) + "." + okTypes[f.type], { type: f.type, lastModified: Date.now() });
        }
        pending.push(f); added++;
      }
      renderShots();
      if (problem) say(problem);
      return added;
    };

    shots.addEventListener("change", function () { addShots(Array.prototype.slice.call(shots.files)); });   /* the button adds to what is already there */
    previews.addEventListener("click", function (e) {
      var b = e.target.closest("[data-remove]");
      if (!b) return;
      pending.splice(parseInt(b.getAttribute("data-remove"), 10), 1);
      renderShots();
    });

    document.addEventListener("paste", function (e) {
      var cd = e.clipboardData, imgs = [], i;
      if (!composerVisible() || !cd || !cd.items) return;
      for (i = 0; i < cd.items.length; i++) {
        if (cd.items[i].kind === "file" && cd.items[i].type.indexOf("image/") === 0) { var f = cd.items[i].getAsFile(); if (f) imgs.push(f); }
      }
      if (!imgs.length) return;                                            /* text only: pasted as it always was */
      var t = e.target;
      if (t && t.closest && t.closest("input, textarea, select, [contenteditable]") && !composerForm.contains(t)) return;
      if (!cd.getData("text/plain")) e.preventDefault();                   /* an image copied from a web page also carries its text: keep that paste, add the image too */
      if (shotLocked()) { say(lockText()); return; }
      if (addShots(imgs)) say(imgs.length > 1 ? "Screenshots added." : "Screenshot added.");
    });

    var hasFiles = function (e) { return !!(e.dataTransfer && Array.prototype.indexOf.call(e.dataTransfer.types || [], "Files") > -1); };
    var endDrag = function () { dragDepth = 0; composerForm.classList.remove("is-drag"); dropHint.hidden = true; };
    document.addEventListener("dragenter", function (e) {
      if (!hasFiles(e) || !composerVisible()) return;
      dragDepth++;
      composerForm.classList.add("is-drag"); dropHint.hidden = false;
    });
    document.addEventListener("dragover", function (e) {
      if (!hasFiles(e)) return;
      e.preventDefault();                                                  /* also outside the chat: a missed drop must not open the image and throw the page away */
      if (e.dataTransfer) e.dataTransfer.dropEffect = composerVisible() && !shotLocked() ? "copy" : "none";
    });
    document.addEventListener("dragleave", function (e) {
      if (!hasFiles(e)) return;
      dragDepth = Math.max(0, dragDepth - 1);
      if (!dragDepth) endDrag();
    });
    document.addEventListener("drop", function (e) {
      if (!hasFiles(e)) return;
      e.preventDefault();
      endDrag();
      if (!composerVisible()) return;
      if (shotLocked()) { say(lockText()); return; }
      if (addShots(Array.prototype.slice.call(e.dataTransfer.files))) say("Screenshot added.");
    });
    renderShots();
  }

  /* while Claude works: ask the server every 3 s, show the step and cost, reload when it is done */
  var runs = document.querySelectorAll("[data-poll]");
  for (var r = 0; r < runs.length; r++) (function (box) {
    var timer = setInterval(function () {
      fetch(box.getAttribute("data-poll"), { headers: { "Accept": "application/json" }, credentials: "same-origin" })
        .then(function (res) { return res.ok ? res.json() : null; })
        .then(function (d) {
          if (!d) return;
          box.querySelector("[data-note]").textContent = d.note;
          box.querySelector("[data-cost]").textContent = d.cost;
          var list = box.querySelector("[data-steps]");
          if (list && d.steps) {
            list.textContent = "";
            for (var k = 0; k < d.steps.length; k++) {
              var li = document.createElement("li"), tm = document.createElement("time");
              tm.textContent = d.steps[k].t; li.appendChild(tm); li.appendChild(document.createTextNode(d.steps[k].text));
              list.appendChild(li);
            }
            list.scrollTop = list.scrollHeight;
          }
          /* Claude asked something: reload once so the question card appears */
          if (d.open_questions && !document.querySelector(".ask-card")) { clearInterval(timer); window.location.reload(); return; }
          if (d.state !== "queued" && d.state !== "working") { clearInterval(timer); window.location.reload(); }
        })
        .catch(function () {});
    }, 3000);
  })(runs[r]);

  /* a beta deploy / sync runs on the server: show its current step, reload when it has finished */
  var job = document.querySelector("[data-job-poll]");
  if (job) {
    var jt = setInterval(function () {
      fetch(job.getAttribute("data-job-poll"), { headers: { "Accept": "application/json" }, credentials: "same-origin" })
        .then(function (res) { return res.ok ? res.json() : null; })
        .then(function (d) {
          if (!d) return;
          job.querySelector("[data-note]").textContent = d.note;
          if (d.state !== "queued" && d.state !== "running") {
            clearInterval(jt);
            window.location.hash = "deploys";
            window.location.reload();
          }
        })
        .catch(function () {});
    }, 3000);
  }

  /* chat composer: the effort list follows the model picked (each model lists the levels it supports) */
  var ccModel = document.getElementById("cc-model"), ccEffort = document.getElementById("cc-effort");
  if (ccModel && ccEffort) {
    var fillEffort = function () {
      var levels = (ccModel.options[ccModel.selectedIndex].getAttribute("data-efforts") || "").split(",").filter(Boolean);
      var keep = ccEffort.value;
      ccEffort.innerHTML = "";
      var none = document.createElement("option");
      none.value = ""; none.textContent = levels.length ? "Effort: default" : "Effort: not available";
      ccEffort.appendChild(none);
      levels.forEach(function (lv) {
        var o = document.createElement("option");
        o.value = lv; o.textContent = "Effort: " + lv;
        ccEffort.appendChild(o);
      });
      ccEffort.value = levels.indexOf(keep) > -1 ? keep : "";
      ccEffort.disabled = !levels.length;
    };
    ccModel.addEventListener("change", fillEffort);
    fillEffort();
    /* the Claude account picked: its own model list (kept on the same model when that account has it too) */
    var ccAccount = document.getElementById("cc-account");
    if (ccAccount) {
      ccAccount.addEventListener("change", function () {
        var tpl = document.getElementById("cc-models-" + ccAccount.value);
        if (!tpl) return;
        var keep = ccModel.value, def = ccModel.getAttribute("data-default") || "";
        ccModel.innerHTML = "";
        ccModel.appendChild(tpl.content.cloneNode(true));
        var vals = [];
        for (var i = 0; i < ccModel.options.length; i++) vals.push(ccModel.options[i].value);
        ccModel.value = vals.indexOf(keep) > -1 ? keep : (vals.indexOf(def) > -1 ? def : (vals[0] || ""));
        fillEffort();
      });
    }
  }

  /* the engine picked in the chat decides which pickers show */
  var engineSel = document.getElementById("chat-engine");
  if (engineSel) {
    var showEngine = function () {
      var boxes = document.querySelectorAll("[data-engine-only]");
      for (var i = 0; i < boxes.length; i++) {
        var only = boxes[i].getAttribute("data-engine-only");
        boxes[i].style.display = (only === "any" || only === engineSel.value) ? "contents" : "none";
      }
    };
    engineSel.addEventListener("change", showEngine);
    showEngine();
  }

  /* Debug mode: Sonnet 5.5 + high effort (still changeable) and a hint on what to write */
  var ccDebug = document.getElementById("cc-debug"), msgBox = document.getElementById("msg");
  if (ccDebug && msgBox) {
    ccDebug.addEventListener("change", function () {
      if (ccDebug.checked) {
        if (ccModel) { for (var i = 0; i < ccModel.options.length; i++) { if (ccModel.options[i].value === "sonnet") { ccModel.selectedIndex = i; break; } } ccModel.dispatchEvent(new Event("change")); }
        if (ccEffort && !ccEffort.disabled) { ccEffort.value = "high"; }
      }
      setHint();
    });
  }

  /* a card with a one-time code: its buttons switch off after the first press (a second press would send the same code again) */
  document.addEventListener("submit", function (e) {
    var f = e.target;
    if (f && f.classList && f.classList.contains("ask-card")) {
      var bs = f.querySelectorAll("button[type=submit]");
      setTimeout(function () { for (var i = 0; i < bs.length; i++) bs[i].disabled = true; }, 0);
    }
  });

  var cur = document.querySelector('[data-pick][aria-pressed="true"]');
  if (cur) pick(cur.getAttribute("data-pick"));
  /* a #hash that names a section inside a tab (e.g. #live-releases) opens that tab and scrolls to it */
  var h = (window.location.hash || "").replace("#", "");
  var target = h && TABS.indexOf(h) === -1 ? document.getElementById(h) : null;
  var holder = target ? target.closest("[data-panel]") : null;
  setTab(holder ? holder.getAttribute("data-panel") : h);
  if (target) setTimeout(function () { target.scrollIntoView({ block: "start" }); }, 50);
});

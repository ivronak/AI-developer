/* Guided tour: plays the narration clips (tour/s1..s6.m4a) and drives the real mock-up in the frame with a fake cursor.
   Each step: caption + audio; its actions run at a fraction of the clip's length. */
document.addEventListener("DOMContentLoaded", function () {
  var $ = function (id) { return document.getElementById(id); };
  var frame = $("screen"), cursor = $("cursor"), ring = $("ring");
  var stopped = false, audio = null;

  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
  function doc() { try { return frame.contentDocument; } catch (e) { return null; } }
  function q(sel) { var d = doc(); return d ? d.querySelector(sel) : null; }

  async function waitFor(sel, ms) {
    var end = Date.now() + (ms || 6000);
    while (Date.now() < end && !stopped) {
      var el = q(sel);
      if (el && el.getClientRects().length) return el;
      await sleep(100);
    }
    return q(sel);
  }
  function nav(url) {
    return new Promise(function (resolve) {
      frame.addEventListener("load", function once() { frame.removeEventListener("load", once); setTimeout(resolve, 350); });
      frame.src = url;
    });
  }

  function screenRect(el) {
    var f = frame.getBoundingClientRect(), r = el.getBoundingClientRect();
    return { left: f.left + r.left, top: f.top + r.top, width: r.width, height: r.height };
  }
  async function point(sel, opts) {
    opts = opts || {};
    var el = typeof sel === "string" ? await waitFor(sel) : sel;
    if (!el || stopped) return null;
    el.scrollIntoView({ block: "center", behavior: "smooth" });
    await sleep(450);
    var r = screenRect(el);
    cursor.style.left = (r.left + Math.min(r.width * 0.5, 60)) + "px";
    cursor.style.top = (r.top + Math.min(r.height * 0.55, 40)) + "px";
    if (opts.ring !== false) {
      ring.hidden = false;
      ring.style.left = (r.left - 6) + "px"; ring.style.top = (r.top - 6) + "px";
      ring.style.width = (r.width + 12) + "px"; ring.style.height = (r.height + 12) + "px";
    }
    await sleep(900);
    return el;
  }
  function hideRing() { ring.hidden = true; }
  async function click(sel) {
    var el = await point(sel, { ring: false });
    if (!el || stopped) return null;
    hideRing();
    var c = $("click"); c.classList.remove("on"); void c.offsetWidth; c.classList.add("on");
    await sleep(180);
    el.click();
    await sleep(350);
    return el;
  }
  async function type(sel, text) {
    var el = await point(sel, { ring: false });
    if (!el || stopped) return;
    el.focus();
    el.value = "";
    for (var i = 0; i < text.length && !stopped; i++) { el.value += text.charAt(i); await sleep(40); }
  }

  var STEPS = [
    { dur: 14.9, text: "यह हमारा नया डैशबोर्ड है। यहाँ सारे प्रोजेक्ट एक ही जगह दिखते हैं। ऊपर के रंगीन बॉक्स बताते हैं कि कौन सा प्रोजेक्ट डाउन है, किस पर ध्यान देना है, और कितने अप्रूवल बाकी हैं। चलिए, पार्किंग मैनेजर प्रोजेक्ट खोलते हैं।",
      actions: [
        [0.05, function () { return point("#kpis"); }],
        [0.32, function () { return point('#kpis [data-filter="bad"]'); }],
        [0.55, function () { return point(".panels .panel"); }],
        [0.82, async function () { await click('.pcard[href="project.html?id=1"]'); await nav("project.html?id=1"); }]
      ] },
    { dur: 18.1, text: "हर प्रोजेक्ट के दो हिस्से हैं। बीटा, यानी टेस्ट वाली साइट, और लाइव, जहाँ असली यूज़र हैं। दोनों का डेटाबेस अलग है। बीटा में लाइव डेटाबेस की ताज़ा कॉपी आती है, लेकिन लाइव डेटाबेस को सीधे कोई नहीं छूता। गिटहब में भी बीटा ब्रांच और मेन ब्रांच अलग रहती हैं।",
      actions: [
        [0.08, function () { return point(".env:not(.env-live)"); }],
        [0.3, function () { return point(".env-live"); }],
        [0.55, function () { return point("#act-sync"); }],
        [0.82, function () { return point("#p-chips .repo"); }]
      ] },
    { dur: 12.0, text: "अब मान लीजिए क्लाइंट का कोई बदलाव आया। हम यहाँ चैट में लिखते हैं, ज़रूरत हो तो स्क्रीनशॉट भी डालते हैं। बदलाव सिर्फ़ बीटा ब्रांच पर होता है, और उसके बाद टेस्ट अपने आप चलते हैं।",
      actions: [
        [0.02, function () { return click('[data-pick=""]'); }],
        [0.2, function () { return type("#msg", "Client wants the logo a little bigger on the login page."); }],
        [0.62, function () { return click('#composer button[type="submit"]'); }],
        [0.88, function () { return point(".ccard"); }]
      ] },
    { dur: 10.5, text: "टेस्ट पास होने पर क्लाइंट को बीटा का लिंक भेजा जाता है। क्लाइंट बिना लॉगिन के लिंक खोलकर बदलाव देखता है, और अप्रूव करता है। उसका अप्रूवल रिकॉर्ड में रहता है।",
      actions: [
        [0.02, function () { return click('[data-do="send"]'); }],
        [0.16, function () { return click('dialog[open] [data-act="0"]'); }],
        [0.32, function () { return point("#share-link"); }],
        [0.5, async function () {
          hideRing();
          var w = frame.contentWindow, s = w.APP.S();
          var c = s.changes.filter(function (x) { return x.projectId === 1 && x.status === "client"; }).sort(function (a, b) { return b.approval.sentAt - a.approval.sentAt; })[0];
          if (c) await nav("approve.html?t=" + c.approval.token);
        }],
        [0.78, function () { return click("#yes"); }]
      ] },
    { dur: 11.7, text: "क्लाइंट के अप्रूव करने के बाद ही बीटा का कोड मेन ब्रांच में जाता है और लाइव होता है। लाइव करने से पहले डेटाबेस का स्नैपशॉट अपने आप बनता है, और यह काम ओटीपी डालकर ही होता है।",
      actions: [
        [0.0, function () { hideRing(); return nav("project.html?id=1"); }],
        [0.18, function () { return click("#act-live"); }],
        [0.42, function () { return type("#dlg-otp", "123456"); }],
        [0.6, function () { return click("#gl-go"); }],
        [0.66, function () { return point("dialog[open] .dlg-body"); }]
      ] },
    { dur: 11.1, text: "अगर लाइव में कोई गड़बड़ हो, तो एक बटन से पिछला वर्ज़न वापस आ जाता है। इस तरह पुराने प्रोजेक्ट में कुछ नहीं बिगड़ता, और हमारा रोज़ का काम आसान और सुरक्षित हो जाता है।",
      actions: [
        [0.0, async function () {
          var end = Date.now() + 5000;
          while (Date.now() < end && !stopped) { var b = q("dialog[open] .dlg-body"); if (!b || b.textContent.indexOf("Live.") === 0) break; await sleep(150); }
          hideRing();
          return click("dialog[open] .dlg-foot [data-close]");
        }],
        [0.25, function () { return point("#act-rollback"); }],
        [0.7, async function () { hideRing(); await nav("projects.html"); await point("#kpis"); }]
      ] }
  ];

  async function runStep(i) {
    var st = STEPS[i], t0 = performance.now();
    $("cap-step").textContent = (i + 1) + " / " + STEPS.length;
    $("cap-text").textContent = st.text;
    $("cap-bar").style.width = "0";
    audio = new Audio("tour/s" + (i + 1) + ".m4a");
    var ended = new Promise(function (r) { audio.addEventListener("ended", r); audio.addEventListener("error", r); });
    var dur = st.dur;
    try { await audio.play(); } catch (e) { audio = null; }
    function elapsed() { return audio && !audio.paused ? audio.currentTime : (performance.now() - t0) / 1000; }
    var bar = setInterval(function () { $("cap-bar").style.width = Math.min(100, elapsed() / dur * 100) + "%"; }, 200);
    for (var k = 0; k < st.actions.length && !stopped; k++) {
      while (!stopped && elapsed() < st.actions[k][0] * dur) await sleep(80);
      if (!stopped) await st.actions[k][1]();
    }
    if (audio && !stopped) await ended;
    else while (!stopped && elapsed() < dur) await sleep(100);
    clearInterval(bar);
    $("cap-bar").style.width = "100%";
    await sleep(500);
  }

  async function run() {
    stopped = false;
    /* fresh demo data, signed in as the owner, card view */
    try {
      localStorage.removeItem("apd.state");
      localStorage.setItem("apd.view", JSON.stringify("grid"));
      sessionStorage.setItem("apd.user", JSON.stringify({ id: 1, name: "Owner", role: "owner" }));
    } catch (e) {}
    $("start").hidden = true;
    $("caption").hidden = false;
    cursor.hidden = false;
    await nav("projects.html");
    for (var i = 0; i < STEPS.length && !stopped; i++) await runStep(i);
    finish(stopped ? "Tour stopped." : "Tour finished.");
  }

  function finish(msg) {
    stopped = true;
    if (audio) { try { audio.pause(); } catch (e) {} }
    hideRing();
    cursor.hidden = true;
    $("caption").hidden = true;
    $("start-note").textContent = msg + " Press Start to play it again.";
    $("go").textContent = "▶ Play again";
    $("start").hidden = false;
  }

  $("go").addEventListener("click", run);
  $("stop").addEventListener("click", function () { finish("Tour stopped."); });
  window.TOUR = { run: run, steps: STEPS };
});

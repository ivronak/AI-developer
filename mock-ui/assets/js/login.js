/* S1 Login: password, then OTP. 5 wrong tries (password or code) lock sign-in for 15 minutes. */
document.addEventListener("DOMContentLoaded", function () {
  var D = window.DEMO, A = window.APP;
  if (A.currentUser()) { window.location.replace("projects.html"); return; }

  var $ = function (id) { return document.getElementById(id); };
  var pending = null;      // user who passed step 1
  var resendTimer = null;
  var lockTimer = null;

  function lockState() { return A.get("apd.lock") || { tries: 0, until: 0 }; }
  function saveLock(s) { A.set("apd.lock", s); }

  function show(step) {
    $("step-password").hidden = step !== "password";
    $("step-otp").hidden = step !== "otp";
    $("step-locked").hidden = step !== "locked";
    $("step1-bar").className = "on";
    $("step2-bar").className = step === "otp" ? "on" : "";
  }

  function showLocked() {
    show("locked");
    clearInterval(lockTimer);
    function tick() {
      var left = lockState().until - Date.now();
      if (left <= 0) { clearInterval(lockTimer); saveLock({ tries: 0, until: 0 }); show("password"); return; }
      var m = Math.floor(left / 60000), s = Math.floor((left % 60000) / 1000);
      $("lock-left").textContent = m + ":" + (s < 10 ? "0" : "") + s;
    }
    tick();
    lockTimer = setInterval(tick, 1000);
  }

  /* Returns true when this failure locked the account. */
  function fail(errorEl, text) {
    var s = lockState();
    s.tries += 1;
    if (s.tries >= D.MAX_TRIES) {
      s.until = Date.now() + D.LOCK_MIN * 60000;
      saveLock(s);
      showLocked();
      return true;
    }
    saveLock(s);
    var left = D.MAX_TRIES - s.tries;
    errorEl.textContent = text + " " + left + (left === 1 ? " try" : " tries") + " left before a 15-minute lock.";
    errorEl.hidden = false;
    return false;
  }

  function findUser(login) {
    var v = login.trim().toLowerCase(), digits = v.replace(/\D/g, "");
    var users = A.S().users;
    for (var i = 0; i < users.length; i++) {
      var u = users[i];
      if (u.email === v) return u;
      if (digits.length >= 10 && u.mobile.replace(/\D/g, "").slice(-10) === digits.slice(-10)) return u;
    }
    return null;
  }

  function startResend() {
    var n = 30, btn = $("otp-resend");
    btn.disabled = true;
    clearInterval(resendTimer);
    btn.textContent = "Resend in " + n + "s";
    resendTimer = setInterval(function () {
      n -= 1;
      if (n <= 0) { clearInterval(resendTimer); btn.disabled = false; btn.textContent = "Resend code"; return; }
      btn.textContent = "Resend in " + n + "s";
    }, 1000);
  }

  $("form-password").addEventListener("submit", function (e) {
    e.preventDefault();
    var err = $("pw-error"); err.hidden = true;
    var login = $("login-id").value, pw = $("login-pw").value;
    if (!login.trim() || !pw) {
      err.textContent = "Enter your email or mobile and your password.";
      err.hidden = false; return;
    }
    var u = findUser(login);
    if (!u || pw !== D.PASSWORD) {
      $("login-pw").value = "";
      fail(err, "Wrong email/mobile or password.");
      return;
    }
    if (!u.active) {
      err.textContent = "This account's access is turned off. Ask the owner.";
      err.hidden = false; return;
    }
    pending = u;
    $("otp-target").textContent = "mobile •••• " + u.mobile.replace(/\D/g, "").slice(-4);
    $("otp").value = "";
    $("otp-error").hidden = true;
    show("otp");
    startResend();
    $("otp").focus();
  });

  $("otp").addEventListener("input", function () {
    this.value = this.value.replace(/\D/g, "").slice(0, 6);
  });

  $("form-otp").addEventListener("submit", function (e) {
    e.preventDefault();
    var err = $("otp-error"); err.hidden = true;
    var code = $("otp").value;
    if (code.length !== 6) { err.textContent = "Enter all 6 digits."; err.hidden = false; return; }
    if (code !== D.OTP) {
      $("otp").value = "";
      fail(err, "That code is not right.");
      return;
    }
    saveLock({ tries: 0, until: 0 });
    A.signIn({ id: pending.id, name: pending.name, role: pending.role });
    pending.lastAt = Date.now();
    A.log("Sign in", 0, "Password + OTP");
    window.location.href = "projects.html";
  });

  $("otp-back").addEventListener("click", function () {
    pending = null; clearInterval(resendTimer);
    $("login-pw").value = "";
    show("password");
  });

  $("otp-resend").addEventListener("click", function () {
    A.toast("New code sent (demo code is still " + D.OTP + ").");
    startResend();
  });

  $("pw-show").innerHTML = A.ICONS.eye;
  $("pw-show").addEventListener("click", function () {
    var inp = $("login-pw"), showing = inp.type === "text";
    inp.type = showing ? "password" : "text";
    this.innerHTML = showing ? A.ICONS.eye : A.ICONS.eyeOff;
    this.setAttribute("aria-label", showing ? "Show password" : "Hide password");
  });

  var demoBtns = document.querySelectorAll("[data-demo]");
  for (var i = 0; i < demoBtns.length; i++) {
    demoBtns[i].addEventListener("click", function () {
      if (!$("step-password").hidden) {
        $("login-id").value = this.getAttribute("data-demo") === "owner" ? "owner@demo.test" : "dev@demo.test";
        $("login-pw").value = D.PASSWORD;
        $("pw-error").hidden = true;
        $("pw-submit").focus();
      }
    });
  }

  $("demo-reset").addEventListener("click", function () {
    A.resetState();
    A.toast("Demo data reset to the start.");
  });

  $("demo-unlock").addEventListener("click", function () {
    saveLock({ tries: 0, until: 0 });
    clearInterval(lockTimer);
    $("pw-error").hidden = true;
    if (!$("step-locked").hidden) show("password");
    A.toast("Lock reset.");
  });

  var s = lockState();
  if (s.until > Date.now()) showLocked(); else show("password");
});

(function () {
  var $ = function (id) { return document.getElementById(id); };

  var token = sessionStorage.getItem("wp_token") || null;
  var curLog = "backend";
  var statusTimer = null;
  var statusSeq = 0;          // drops stale/overlapping poll responses

  // The server's `ops` are the single source of truth for what is running and
  // for results; the client never invents them. Keyed by lane
  // ("backend" / "frontend" / "all"): backend and frontend are independent, so
  // each gets its own toast and seen-timestamp; "all" (and redeploy) holds both.
  //   running  — lanes the latest poll reports running
  //   pending  — lanes we just fired, awaiting the server's reflection; this
  //              alone disables buttons so a second click can't race a poll
  //   seenTs   — last terminal ts already surfaced, so a stale result on the
  //              daemon at load (or one already shown) never re-pops
  var running = {};
  var pending = {};
  var seenTs = {};
  var initialized = false;
  var opEls = {};
  var opTimers = {};

  function authHeaders(extra) {
    var h = extra || {};
    if (token) h["Authorization"] = "Bearer " + token;
    return h;
  }

  async function api(path, opts) {
    opts = opts || {};
    opts.headers = authHeaders(opts.headers);
    var res = await fetch(path, opts);
    if (res.status === 401) { logout(); throw new Error("session expired"); }
    return res;
  }

  // ── auth ──
  async function login() {
    var pw = $("pw").value;
    if (!pw) { $("loginErr").textContent = "Enter the password."; return; }
    $("loginBtn").disabled = true;
    $("loginErr").textContent = "";
    try {
      var res = await fetch("/api/login", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: pw }),
      });
      var data = await res.json().catch(function () { return {}; });
      if (res.ok && data.token) {
        token = data.token; sessionStorage.setItem("wp_token", token);
        $("pw").value = ""; enterConsole();
      } else {
        $("loginErr").textContent = data.error || ("Failed (HTTP " + res.status + ")");
      }
    } catch (e) { $("loginErr").textContent = "Request failed."; }
    finally { $("loginBtn").disabled = false; }
  }

  function logout() {
    token = null; sessionStorage.removeItem("wp_token");
    if (statusTimer) { clearInterval(statusTimer); statusTimer = null; }
    setBeat(false);
    $("console").classList.add("hidden");
    $("login").classList.remove("hidden");
  }

  function enterConsole() {
    running = {}; pending = {}; seenTs = {}; initialized = false;
    for (var k in opEls) clearOp(k);
    $("login").classList.add("hidden");
    $("console").classList.remove("hidden");
    refreshStatus(); loadLog();
    statusTimer = setInterval(refreshStatus, 3000);
  }

  // ── connection ──
  function setBeat(live) {
    $("beat").classList.toggle("live", live);
    $("beatTxt").textContent = live ? "live" : "offline";
  }

  // ── busy lanes (a lane is busy if fired-but-unconfirmed or server-running) ──
  function active(key) { return !!pending[key] || !!running[key]; }
  function anyActive() {
    for (var k in pending) if (pending[k]) return true;
    for (var k in running) if (running[k]) return true;
    return false;
  }
  function laneBusy(target) {
    if (active("all")) return true;
    return target === "all" ? anyActive() : active(target);
  }
  function applyBusy() {
    var allBusy = active("all");
    for (var name in cards) {
      var c = cards[name];
      if (!c.toggle) continue;
      var d = allBusy || active(name);
      c.toggle.disabled = d; c.restart.disabled = d;
    }
    var rb = document.querySelectorAll(".redeploy button");
    var anyB = anyActive();           // redeploy needs both lanes free
    for (var i = 0; i < rb.length; i++) rb[i].disabled = anyB;
  }

  // ── operation toasts (one per lane, running -> ok/failed in place) ──
  function showOp(key, kind, label, msg) {
    if (opTimers[key]) { clearTimeout(opTimers[key]); delete opTimers[key]; }
    var el = opEls[key];
    if (!el) {
      el = document.createElement("div");
      el.className = "toast";
      el.innerHTML =
        '<span class="tdot"></span>' +
        '<div class="tbody"><div class="ttitle"></div><div class="tmsg"></div></div>' +
        '<button class="x" aria-label="dismiss">×</button>';
      el.querySelector(".x").addEventListener("click", function () { clearOp(key); });
      opEls[key] = el;
      $("toasts").appendChild(el);
      requestAnimationFrame(function () { el.classList.add("in"); });
    }
    var run = kind === "running";
    el.classList.remove("run", "ok", "err");
    el.classList.add(run ? "run" : kind === "ok" ? "ok" : "err");
    el.querySelector(".ttitle").textContent = label;
    var m = el.querySelector(".tmsg");
    m.textContent = msg || "";
    m.style.display = msg ? "" : "none";
    el.querySelector(".x").style.display = run ? "none" : "";

    var oldBar = el.querySelector(".tbar");
    if (oldBar) oldBar.remove();
    if (kind === "ok") {              // success self-dismisses with a drain bar
      var bar = document.createElement("span"); bar.className = "tbar";
      el.appendChild(bar);
      opTimers[key] = setTimeout(function () { clearOp(key); }, 5000);
    }
  }

  function clearOp(key) {
    if (opTimers[key]) { clearTimeout(opTimers[key]); delete opTimers[key]; }
    var el = opEls[key];
    if (!el) return;
    delete opEls[key];
    el.classList.remove("in");
    setTimeout(function () { el.remove(); }, 220);
  }

  // A one-off toast for a client-side request error (network / 5xx) — same
  // component, but not tied to a lane's op lifecycle.
  function errorToast(label, msg) {
    var el = document.createElement("div");
    el.className = "toast err";
    el.innerHTML =
      '<span class="tdot"></span>' +
      '<div class="tbody"><div class="ttitle"></div><div class="tmsg"></div></div>' +
      '<button class="x" aria-label="dismiss">×</button>';
    el.querySelector(".ttitle").textContent = label;
    var m = el.querySelector(".tmsg");
    m.textContent = msg || ""; m.style.display = msg ? "" : "none";
    function close() { el.classList.remove("in"); setTimeout(function () { el.remove(); }, 220); }
    el.querySelector(".x").addEventListener("click", close);
    $("toasts").appendChild(el);
    requestAnimationFrame(function () { el.classList.add("in"); });
    setTimeout(close, 6000);
  }

  // ── service cards (built once, reconciled in place) ──
  var cards = {};

  function buildCard(name) {
    var el = document.createElement("div");
    el.className = "svc";
    el.setAttribute("data-name", name);
    el.innerHTML =
      '<span class="led"></span>' +
      '<div class="meta"><div class="name"></div><div class="sub"></div></div>';
    el.querySelector(".name").textContent = name;
    var subEl = el.querySelector(".sub");

    var card = { el: el, sub: subEl, restart: null, toggle: null };
    if (name !== "caffeinate") {
      var acts = document.createElement("div");
      acts.className = "acts";
      var restart = document.createElement("button");
      restart.textContent = "restart";
      restart.addEventListener("click", function () { doAction("restart", name); });
      var toggle = document.createElement("button");
      toggle.addEventListener("click", function () {
        doAction(toggle.getAttribute("data-act"), name);
      });
      acts.appendChild(restart); acts.appendChild(toggle);
      el.appendChild(acts);
      card.restart = restart; card.toggle = toggle;
    }
    return card;
  }

  function ledFor(s) {
    if (s.state !== "running") return "down";
    if (s.health === "unhealthy") return "warn";
    return "up";
  }
  function subFor(s) {
    if (s.state !== "running") return s.state;
    var bits = [];
    if (s.pid) bits.push("pid " + s.pid);
    if (s.port) bits.push(":" + s.port);
    if (s.health) bits.push(s.health);
    return bits.join("  ·  ");
  }

  function renderServices(list) {
    var present = {};
    for (var i = 0; i < list.length; i++) {
      var s = list[i];
      present[s.name] = true;
      var card = cards[s.name];
      if (!card) { card = buildCard(s.name); cards[s.name] = card; $("services").appendChild(card.el); }
      card.el.setAttribute("data-led", ledFor(s));
      card.sub.textContent = subFor(s);
      if (card.toggle) {
        var act = s.state === "running" ? "stop" : "start";
        card.toggle.setAttribute("data-act", act);
        card.toggle.textContent = act;
      }
    }
    for (var name in cards) {
      if (!present[name]) { cards[name].el.remove(); delete cards[name]; }
    }
  }

  // ── status poll ──
  async function refreshStatus() {
    var seq = ++statusSeq;
    try {
      var res = await api("/api/status");
      var data = await res.json();
      if (seq !== statusSeq) return;  // a newer poll superseded this one
      setBeat(true);
      renderServices(data.services || []);
      handleOps(data.ops || []);
      if (anyActive()) loadLog();     // watch the rebuild while an op runs
      initialized = true;
    } catch (e) { if (seq === statusSeq) setBeat(false); }
  }

  function opLabel(op) { return op.action + " · " + op.target; }

  function handleOps(list) {
    var next = {};
    for (var i = 0; i < list.length; i++) {
      var op = list[i];
      if (op.state === "running") {
        next[op.key] = true;
        delete pending[op.key];       // the server now owns this lane's state
        showOp(op.key, "running", opLabel(op), "");
      } else if (!initialized) {
        seenTs[op.key] = op.ts;       // suppress whatever was sitting there at load
      } else if (op.ts !== seenTs[op.key]) {
        seenTs[op.key] = op.ts;
        delete pending[op.key];
        showOp(op.key, op.state, opLabel(op), op.state === "failed" ? op.message : "");
      }
    }
    running = next;
    applyBusy();
  }

  // ── operations ──
  async function doAction(action, target) {
    if (laneBusy(target)) return;
    if (target === "backend" || target === "all") {
      if (!confirm(action + " " + target + "? This interrupts running sessions."))
        return;
    }
    fire("/api/action", { action: action, target: target }, target);
  }

  async function redeploy(channel) {
    if (anyActive()) return;          // redeploy needs both lanes
    var what = channel === "current"
      ? "Restart the whole stack from the current checkout?"
      : "Redeploy " + channel + ": pull and restart the whole stack?";
    if (!confirm(what + " This interrupts running sessions.")) return;
    fire("/api/redeploy", { channel: channel }, "all");
  }

  // Fire only touches `pending` (to disable the lane immediately) and re-syncs;
  // the toast and running state come solely from the poll, so a rejected or
  // failed request can never corrupt a genuinely running op.
  async function fire(path, payload, key) {
    pending[key] = true; applyBusy();
    try {
      var res = await api(path, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (res.status !== 202) {
        delete pending[key]; applyBusy();
        if (res.status !== 409) {     // 409 just means a lane is busy; the poll shows it
          var data = await res.json().catch(function () { return {}; });
          errorToast("request failed", data.error || ("HTTP " + res.status));
        }
      }
      refreshStatus();
    } catch (e) { delete pending[key]; applyBusy(); }
  }

  // ── logs ──
  async function loadLog() {
    try {
      var res = await api("/api/logs?target=" + curLog + "&n=200");
      var data = await res.json();
      var pre = $("log");
      var atBottom = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 24;
      pre.textContent = (data.lines || []).join("\n");
      if (atBottom) pre.scrollTop = pre.scrollHeight;
    } catch (e) {}
  }

  // ── wiring ──
  $("loginBtn").addEventListener("click", login);
  $("pw").addEventListener("keydown", function (e) { if (e.key === "Enter") login(); });
  $("logRefresh").addEventListener("click", loadLog);

  var chans = document.querySelectorAll("button[data-channel]");
  for (var r = 0; r < chans.length; r++) {
    chans[r].addEventListener("click", function () {
      redeploy(this.getAttribute("data-channel"));
    });
  }

  var logBtns = $("logSeg").querySelectorAll("button[data-log]");
  for (var k = 0; k < logBtns.length; k++) {
    logBtns[k].addEventListener("click", function () {
      curLog = this.getAttribute("data-log");
      for (var m = 0; m < logBtns.length; m++) logBtns[m].classList.remove("on");
      this.classList.add("on");
      loadLog();
    });
  }

  if (token) enterConsole(); else setBeat(false);
})();

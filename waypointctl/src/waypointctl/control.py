"""Out-of-band remote control hosted by waypointd.

A minimal, dependency-free HTTP console that lets a phone drive the whole
stack/process layer — status, logs, lifecycle, redeploy — when the frontend
build is corrupt or the backend has crashed. It lives in the supervisor
(waypointd), not in either managed service, so it survives a broken frontend
*and* a broken backend.

It is deliberately a *stack* console, not a reimplementation of the app's
session UI: its blast radius is exactly what `waypointctl` can already do.
The action set is a fixed allowlist; there is no arbitrary command execution.
"""

import hmac
import json
import secrets
import threading
import time
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from waypointctl import commands
from waypointctl.services import ServiceResult
from waypointctl.stack import WaypointStack
from waypointctl.update import run as run_update

# A blank or default password would expose an unauthenticated control surface
# on the tailnet, so the console refuses to act until a real one is set.
INSECURE_PASSWORDS = frozenset({"", "change-me"})

TOKEN_TTL_SECONDS = 30 * 60
LOGIN_WINDOW_SECONDS = 60.0
LOGIN_MAX_FAILURES = 5
DEFAULT_LOG_LINES = 200

# stable = latest release tag; nightly = tip of main; current = restart the
# checked-out tree with no git update (the channel that works on a dirty/
# unmanaged dev checkout).
REDEPLOY_CHANNELS = ("stable", "nightly", "current")


class ControlServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self, address: tuple[str, int], stack: WaypointStack, password: str | None
    ) -> None:
        self.stack = stack
        self.password = password
        self._lock = threading.Lock()
        self._tokens: dict[str, float] = {}
        self._login_failures: list[float] = []
        # One mutating op at a time; status/logs stay lock-free.
        self._op_lock = threading.Lock()
        self._op_thread: threading.Thread | None = None
        self.last_op: dict[str, object] | None = None
        super().__init__(address, ControlHandler)

    # ── auth ──────────────────────────────────────────────────────────
    @property
    def password_ready(self) -> bool:
        return self.password is not None and self.password not in INSECURE_PASSWORDS

    def login_locked(self) -> bool:
        with self._lock:
            self._prune_failures()
            return len(self._login_failures) >= LOGIN_MAX_FAILURES

    def login(self, supplied: object) -> str | None:
        if not self.password_ready:
            return None
        ok = isinstance(supplied, str) and hmac.compare_digest(
            supplied, self.password or ""
        )
        with self._lock:
            self._prune_failures()
            if not ok:
                self._login_failures.append(time.monotonic())
                return None
            self._login_failures.clear()
            token = secrets.token_urlsafe(32)
            self._tokens[token] = time.monotonic() + TOKEN_TTL_SECONDS
            return token

    def token_valid(self, header: str | None) -> bool:
        if not header or not header.startswith("Bearer "):
            return False
        token = header[len("Bearer ") :]
        now = time.monotonic()
        with self._lock:
            expiry = self._tokens.get(token)
            if expiry is None:
                return False
            if expiry < now:
                self._tokens.pop(token, None)
                return False
            return True

    def _prune_failures(self) -> None:
        cutoff = time.monotonic() - LOGIN_WINDOW_SECONDS
        self._login_failures = [t for t in self._login_failures if t >= cutoff]

    # ── operations ────────────────────────────────────────────────────
    def start_op(
        self,
        action: str,
        target: str,
        run: Callable[[commands.LogFn], ServiceResult],
    ) -> bool:
        """Kick off a mutating op on a worker thread. False if one is running."""
        if not self._op_lock.acquire(blocking=False):
            return False
        self._set_last_op(action, target, "running", "")

        def worker() -> None:
            lines: list[str] = []
            state, message = "failed", ""
            try:
                result = run(lambda _stream, line: lines.append(line))
                state = "ok" if result.ok else "failed"
                message = result.message or "\n".join(lines[-6:])
            except Exception as exc:  # noqa: BLE001
                message = str(exc)
            finally:
                # Record the terminal state before releasing the lock, so a
                # waiting op can't acquire it and flip last_op to "running"
                # only to have this worker clobber it with the finished result.
                self._set_last_op(action, target, state, message)
                print(f"[control] {action} {target} -> {state}", flush=True)
                self._op_lock.release()

        thread = threading.Thread(target=worker, name=f"control-{action}", daemon=True)
        self._op_thread = thread
        thread.start()
        return True

    def redeploy(self, channel: str, log: commands.LogFn) -> ServiceResult:
        # `current` redeploys the checked-out tree as-is — the only channel that
        # works on an unmanaged/dirty repo. `stable`/`nightly` git-update first;
        # an unmanaged or dirty tree surfaces git's own refusal and the stack is
        # left untouched (no restart on a failed update).
        if channel != "current":
            try:
                run_update(self.stack.config.home, nightly=channel == "nightly")
            except Exception as exc:  # noqa: BLE001
                return ServiceResult(ok=False, message=f"update failed: {exc}")
        return commands.run_action(self.stack, "restart", "all", log)

    def join_op(self, timeout: float | None = None) -> None:
        thread = self._op_thread
        if thread is not None:
            thread.join(timeout=timeout)

    def _set_last_op(self, action: str, target: str, state: str, message: str) -> None:
        with self._lock:
            self.last_op = {
                "action": action,
                "target": target,
                "state": state,
                "message": message,
                "ts": time.time(),
            }


class ControlHandler(BaseHTTPRequestHandler):
    server_version = "waypointd-control"

    @property
    def control(self) -> ControlServer:
        server = self.server
        assert isinstance(server, ControlServer)
        return server

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return

    # ── routing ───────────────────────────────────────────────────────
    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path in ("/", "/index.html"):
            self._send_html(HTTPStatus.OK, _PAGE)
        elif path == "/health":
            self._send_json(HTTPStatus.OK, {"status": "ok"})
        elif path == "/api/status":
            self._handle_status()
        elif path == "/api/logs":
            self._handle_logs()
        else:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/api/login":
            self._handle_login()
        elif path == "/api/action":
            self._handle_action()
        elif path == "/api/redeploy":
            self._handle_redeploy()
        else:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    # ── handlers ──────────────────────────────────────────────────────
    def _handle_login(self) -> None:
        if not self.control.password_ready:
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": "set WAYPOINT_PASSWORD to enable remote control"},
            )
            return
        if self.control.login_locked():
            self._send_json(
                HTTPStatus.TOO_MANY_REQUESTS,
                {"error": "too many attempts; wait a minute"},
            )
            return
        body = self._read_json_body()
        if body is None:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid JSON body"})
            return
        token = self.control.login(body.get("password"))
        if token is None:
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "wrong password"})
            return
        self._send_json(
            HTTPStatus.OK, {"token": token, "expires_in": TOKEN_TTL_SECONDS}
        )

    def _handle_status(self) -> None:
        if not self._authorized():
            return
        self._send_json(
            HTTPStatus.OK,
            {
                "services": commands.status_payload(self.control.stack),
                "last_op": self.control.last_op,
            },
        )

    def _handle_logs(self) -> None:
        if not self._authorized():
            return
        params = parse_qs(urlsplit(self.path).query)
        target = (params.get("target", ["backend"])[0]).lower()
        try:
            count = int(params.get("n", [str(DEFAULT_LOG_LINES)])[0])
        except ValueError:
            count = DEFAULT_LOG_LINES
        try:
            lines = commands.tail_log(target, count)
        except ValueError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        self._send_json(HTTPStatus.OK, {"target": target, "lines": lines})

    def _handle_action(self) -> None:
        if not self._authorized():
            return
        body = self._read_json_body()
        if body is None:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid JSON body"})
            return
        action = body.get("action")
        target = body.get("target")
        if action not in commands.ACTIONS:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": f"action must be one of {', '.join(commands.ACTIONS)}"},
            )
            return
        if target not in ("frontend", "backend", "all"):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "target must be frontend, backend, or all"},
            )
            return
        stack = self.control.stack
        started = self.control.start_op(
            action,
            target,
            lambda log: commands.run_action(stack, action, target, log),
        )
        self._respond_started(started, action, target)

    def _handle_redeploy(self) -> None:
        if not self._authorized():
            return
        body = self._read_json_body()
        if body is None:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid JSON body"})
            return
        channel = body.get("channel")
        if channel not in REDEPLOY_CHANNELS:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": f"channel must be one of {', '.join(REDEPLOY_CHANNELS)}"},
            )
            return
        started = self.control.start_op(
            "redeploy",
            channel,
            lambda log: self.control.redeploy(channel, log),
        )
        self._respond_started(started, "redeploy", channel)

    def _respond_started(self, started: bool, action: str, target: str) -> None:
        if not started:
            self._send_json(
                HTTPStatus.CONFLICT, {"error": "an operation is already running"}
            )
            return
        self._send_json(
            HTTPStatus.ACCEPTED, {"accepted": True, "action": action, "target": target}
        )

    # ── helpers ───────────────────────────────────────────────────────
    def _authorized(self) -> bool:
        if self.control.token_valid(self.headers.get("Authorization")):
            return True
        self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
        return False

    def _read_json_body(self) -> dict[str, object] | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            parsed = json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return parsed if isinstance(parsed, dict) else None

    def _send_json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        self._send_bytes(
            status, "application/json", json.dumps(payload).encode("utf-8")
        )

    def _send_html(self, status: HTTPStatus, html: str) -> None:
        self._send_bytes(status, "text/html; charset=utf-8", html.encode("utf-8"))

    def _send_bytes(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass


_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
<meta name="robots" content="noindex" />
<meta name="color-scheme" content="dark" />
<title>WAYPOINT · stack control</title>
<style>
  :root {
    --bg: #07090c;
    --panel: #0d1117;
    --panel-2: #11171f;
    --line: #1e2733;
    --ink: #d7e0ea;
    --ink-dim: #6b7a8d;
    --ink-faint: #43505f;
    --amber: #ffb454;
    --amber-dim: #8a6526;
    --green: #3fb950;
    --red: #f85149;
    --mono: ui-monospace, "SF Mono", "JetBrains Mono", "Cascadia Code", Menlo,
            Consolas, "Liberation Mono", monospace;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; height: 100%; }
  body {
    background:
      radial-gradient(120% 80% at 50% -10%, rgba(255,180,84,0.06), transparent 60%),
      linear-gradient(0deg, var(--bg), var(--bg));
    color: var(--ink);
    font-family: var(--mono);
    font-size: 14px;
    line-height: 1.45;
    -webkit-font-smoothing: antialiased;
    padding: env(safe-area-inset-top) env(safe-area-inset-right)
             env(safe-area-inset-bottom) env(safe-area-inset-left);
  }
  /* faint engineering grid */
  body::before {
    content: ""; position: fixed; inset: 0; pointer-events: none; z-index: 0;
    background-image:
      linear-gradient(var(--line) 1px, transparent 1px),
      linear-gradient(90deg, var(--line) 1px, transparent 1px);
    background-size: 30px 30px;
    opacity: 0.18;
    -webkit-mask-image: radial-gradient(120% 100% at 50% 0%, #000 30%, transparent 75%);
            mask-image: radial-gradient(120% 100% at 50% 0%, #000 30%, transparent 75%);
  }
  .wrap { position: relative; z-index: 1; max-width: 720px; margin: 0 auto; padding: 22px 18px 40px; }

  header { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap;
           border-bottom: 1px solid var(--line); padding-bottom: 14px; }
  .mark { font-size: 19px; font-weight: 700; letter-spacing: 0.28em; }
  .mark b { color: var(--amber); }
  .tag { color: var(--ink-faint); font-size: 11px; letter-spacing: 0.18em;
         text-transform: uppercase; }
  .beat { margin-left: auto; display: flex; align-items: center; gap: 7px;
          color: var(--ink-dim); font-size: 11px; letter-spacing: 0.12em; }
  .beat .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--ink-faint); }
  .beat.live .dot { background: var(--green); box-shadow: 0 0 8px var(--green);
                    animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100% { opacity: 1 } 50% { opacity: 0.35 } }

  .stagger > * { opacity: 0; transform: translateY(8px); animation: rise .5s ease forwards; }
  .stagger > *:nth-child(1) { animation-delay: .04s }
  .stagger > *:nth-child(2) { animation-delay: .10s }
  .stagger > *:nth-child(3) { animation-delay: .16s }
  .stagger > *:nth-child(4) { animation-delay: .22s }
  .stagger > *:nth-child(5) { animation-delay: .28s }
  @keyframes rise { to { opacity: 1; transform: none } }
  @media (prefers-reduced-motion: reduce) {
    .stagger > * { animation: none; opacity: 1; transform: none }
    .beat.live .dot { animation: none }
  }

  .label { color: var(--ink-faint); font-size: 11px; letter-spacing: 0.18em;
           text-transform: uppercase; margin: 26px 2px 10px; }

  /* login */
  #login { margin-top: 14vh; }
  #login .card { background: var(--panel); border: 1px solid var(--line);
                 border-radius: 12px; padding: 26px 22px;
                 box-shadow: 0 30px 80px -40px #000; }
  #login h1 { font-size: 14px; letter-spacing: 0.1em; margin: 0 0 4px; color: var(--ink); }
  #login p { color: var(--ink-dim); font-size: 12px; margin: 0 0 20px; }
  input {
    width: 100%; padding: 13px 14px; font: inherit; color: var(--ink);
    background: var(--bg); border: 1px solid var(--line); border-radius: 9px;
    letter-spacing: 0.04em;
  }
  input:focus { outline: none; border-color: var(--amber-dim);
                box-shadow: 0 0 0 3px rgba(255,180,84,0.12); }

  button {
    font: inherit; color: var(--ink); background: var(--panel-2);
    border: 1px solid var(--line); border-radius: 9px; padding: 11px 14px;
    cursor: pointer; letter-spacing: 0.06em; transition: border-color .15s, background .15s;
    -webkit-tap-highlight-color: transparent;
  }
  button:hover:not(:disabled) { border-color: var(--ink-faint); }
  button:active:not(:disabled) { transform: translateY(1px); }
  button:disabled { opacity: 0.4; cursor: default; }
  button.amber { background: var(--amber); color: #1a1206; border-color: var(--amber);
                 font-weight: 700; }
  button.amber:hover:not(:disabled) { filter: brightness(1.08); }
  button.danger { border-color: #4d2422; color: #ffb1ab; }
  button.danger:hover:not(:disabled) { background: #1d1413; border-color: var(--red); }
  button.block { width: 100%; }

  /* service cards */
  .svc { display: flex; align-items: center; gap: 14px;
         background: var(--panel); border: 1px solid var(--line);
         border-radius: 11px; padding: 14px 16px; margin-bottom: 10px; }
  .led { width: 11px; height: 11px; border-radius: 50%; background: var(--ink-faint);
         flex: none; box-shadow: 0 0 0 0 transparent; }
  .led.up { background: var(--green); box-shadow: 0 0 10px var(--green); }
  .led.down { background: var(--red); box-shadow: 0 0 10px var(--red); }
  .led.warn { background: var(--amber); box-shadow: 0 0 10px var(--amber);
              animation: pulse 1.1s infinite; }
  .svc .meta { min-width: 0; }
  .svc .name { font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; }
  .svc .sub { color: var(--ink-dim); font-size: 11.5px; }
  .svc .acts { margin-left: auto; display: flex; gap: 6px; }
  .svc .acts button { padding: 8px 11px; font-size: 12px; }

  .logs { background: #05070a; border: 1px solid var(--line); border-radius: 11px;
          overflow: hidden; }
  .logs .bar { display: flex; gap: 4px; padding: 8px; border-bottom: 1px solid var(--line);
               align-items: center; }
  .logs .bar .seg { display: flex; gap: 4px; }
  .logs .bar button { padding: 6px 12px; font-size: 12px; }
  .logs .bar button.on { background: var(--panel-2); border-color: var(--ink-faint);
                         color: var(--amber); }
  .logs .bar .spacer { margin-left: auto; }
  pre#log { margin: 0; padding: 12px 14px; max-height: 46vh; overflow: auto;
            font-size: 12px; line-height: 1.5; color: #aebccb; white-space: pre-wrap;
            word-break: break-word; }
  pre#log:empty::before { content: "no output"; color: var(--ink-faint); }

  .danger-zone { border: 1px solid #2a1a18; background: linear-gradient(0deg,#0e0a0a,#0d1117);
                 border-radius: 11px; padding: 14px 16px; }
  .danger-zone .hd { color: #ffb1ab; font-size: 11px; letter-spacing: 0.18em;
                     text-transform: uppercase; margin-bottom: 4px; }
  .danger-zone p { color: var(--ink-dim); font-size: 12px; margin: 0 0 12px; }
  .danger-zone p b { color: #ffb1ab; font-weight: 600; }
  .redeploy { display: flex; gap: 6px; }
  .redeploy button { flex: 1; }

  #op { position: sticky; bottom: 8px; margin-top: 22px; padding: 11px 14px;
        border-radius: 10px; border: 1px solid var(--line); background: var(--panel);
        font-size: 12.5px; display: none; box-shadow: 0 18px 40px -28px #000; }
  #op.show { display: block; }
  #op.run { border-color: var(--amber-dim); }
  #op.ok { border-color: #1f5a2a; }
  #op.err { border-color: #4d2422; }
  #op .st { letter-spacing: 0.1em; text-transform: uppercase; font-weight: 700; }
  #op.run .st { color: var(--amber); } #op.ok .st { color: var(--green); }
  #op.err .st { color: var(--red); }
  #op .msg { color: var(--ink-dim); white-space: pre-wrap; word-break: break-word;
             margin-top: 4px; max-height: 9em; overflow: auto; }

  .hidden { display: none !important; }
  .err-line { color: var(--red); font-size: 12px; margin-top: 12px; min-height: 1em; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <span class="mark">WAY<b>·</b>POINT</span>
    <span class="tag">stack&nbsp;control</span>
    <span class="beat" id="beat"><span class="dot"></span><span id="beatTxt">offline</span></span>
  </header>

  <section id="login">
    <div class="card">
      <h1>AUTHENTICATE</h1>
      <p>Out-of-band control plane. Enter the Waypoint password.</p>
      <input id="pw" type="password" autocomplete="current-password"
             placeholder="WAYPOINT_PASSWORD" />
      <div class="err-line" id="loginErr"></div>
      <button class="amber block" id="loginBtn" style="margin-top:14px">Unlock</button>
    </div>
  </section>

  <main id="console" class="hidden stagger">
    <div class="label">Services</div>
    <div id="services"></div>

    <div class="label">Logs</div>
    <div class="logs">
      <div class="bar">
        <div class="seg">
          <button data-log="backend" class="on">backend</button>
          <button data-log="frontend">frontend</button>
        </div>
        <div class="spacer"></div>
        <button id="logRefresh">refresh</button>
      </div>
      <pre id="log"></pre>
    </div>

    <div class="label">Danger zone</div>
    <div class="danger-zone">
      <div class="hd">Redeploy</div>
      <p>Each restarts the whole stack and interrupts every running session.
         <b>Stable</b> / <b>Nightly</b> git-update first (fail safe on a dirty or
         unmanaged checkout); <b>Current</b> just restarts the checked-out tree.</p>
      <div class="redeploy">
        <button class="danger" data-channel="stable">Stable</button>
        <button class="danger" data-channel="nightly">Nightly</button>
        <button class="danger" data-channel="current">Current</button>
      </div>
    </div>
  </main>

  <div id="op">
    <span class="st" id="opSt"></span> <span id="opTitle"></span>
    <div class="msg" id="opMsg"></div>
  </div>
</div>

<script>
(function () {
  var token = sessionStorage.getItem("wp_token") || null;
  var curLog = "backend";
  var statusTimer = null;

  var $ = function (id) { return document.getElementById(id); };

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
    $("login").classList.add("hidden");
    $("console").classList.remove("hidden");
    refreshStatus(); loadLog();
    statusTimer = setInterval(refreshStatus, 3000);
  }

  // ── status ──
  function setBeat(live) {
    $("beat").classList.toggle("live", live);
    $("beatTxt").textContent = live ? "live" : "offline";
  }

  function ledClass(s) {
    if (s.state !== "running") return "down";
    if (s.health === "unhealthy") return "warn";
    return "up";
  }
  function subLine(s) {
    if (s.state !== "running") return s.state;
    var bits = [];
    if (s.pid) bits.push("pid " + s.pid);
    if (s.port) bits.push("port " + s.port);
    if (s.health) bits.push(s.health);
    return bits.join("  ·  ");
  }

  function renderServices(list) {
    var html = "";
    for (var i = 0; i < list.length; i++) {
      var s = list[i];
      var acts = s.name === "caffeinate" ? "" :
        '<div class="acts">' +
          '<button data-act="restart" data-t="' + s.name + '">restart</button>' +
          '<button data-act="' + (s.state === "running" ? "stop" : "start") + '" data-t="' + s.name + '">' +
            (s.state === "running" ? "stop" : "start") + '</button>' +
        '</div>';
      html +=
        '<div class="svc">' +
          '<span class="led ' + ledClass(s) + '"></span>' +
          '<div class="meta"><div class="name">' + s.name + '</div>' +
          '<div class="sub">' + subLine(s) + '</div></div>' + acts +
        '</div>';
    }
    $("services").innerHTML = html;
    var btns = $("services").querySelectorAll("button[data-act]");
    for (var j = 0; j < btns.length; j++) {
      btns[j].addEventListener("click", function () {
        doAction(this.getAttribute("data-act"), this.getAttribute("data-t"));
      });
    }
  }

  async function refreshStatus() {
    try {
      var res = await api("/api/status");
      var data = await res.json();
      renderServices(data.services || []);
      renderOp(data.last_op);
      setBeat(true);
    } catch (e) { setBeat(false); }
  }

  // ── operations ──
  function renderOp(op) {
    var box = $("op");
    if (!op) { box.className = ""; box.classList.remove("show"); return; }
    box.classList.add("show");
    box.classList.remove("run", "ok", "err");
    box.classList.add(op.state === "running" ? "run" : op.state === "ok" ? "ok" : "err");
    $("opSt").textContent = op.state === "running" ? "working" : op.state;
    $("opTitle").textContent = op.action + " · " + op.target;
    $("opMsg").textContent = op.message || "";
  }

  async function doAction(action, target) {
    if (target === "backend" || target === "all") {
      if (!confirm(action + " " + target + "? This interrupts running sessions."))
        return;
    }
    await fire("/api/action", { action: action, target: target });
  }

  async function redeploy(channel) {
    var what = channel === "current"
      ? "Restart the whole stack from the current checkout?"
      : "Redeploy " + channel + ": pull and restart the whole stack?";
    if (!confirm(what + " This interrupts running sessions.")) return;
    await fire("/api/redeploy", { channel: channel });
  }

  async function fire(path, payload) {
    try {
      var res = await api(path, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      var data = await res.json().catch(function () { return {}; });
      if (res.status === 202) { refreshStatus(); }
      else { renderOp({ action: payload.action || "redeploy",
                        target: payload.target || payload.channel || "all",
                        state: "failed",
                        message: data.error || ("HTTP " + res.status) }); }
    } catch (e) { /* logout already handled */ }
  }

  // ── logs ──
  async function loadLog() {
    try {
      var res = await api("/api/logs?target=" + curLog + "&n=200");
      var data = await res.json();
      var pre = $("log");
      pre.textContent = (data.lines || []).join("\n");
      pre.scrollTop = pre.scrollHeight;
    } catch (e) {}
  }

  // ── wiring ──
  $("loginBtn").addEventListener("click", login);
  $("pw").addEventListener("keydown", function (e) { if (e.key === "Enter") login(); });
  $("logRefresh").addEventListener("click", loadLog);
  var redeployBtns = document.querySelectorAll("button[data-channel]");
  for (var r = 0; r < redeployBtns.length; r++) {
    redeployBtns[r].addEventListener("click", function () {
      redeploy(this.getAttribute("data-channel"));
    });
  }
  var logBtns = document.querySelectorAll("button[data-log]");
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
</script>
</body>
</html>
"""

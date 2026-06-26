"""Out-of-band remote control hosted by waypointd.

A minimal, dependency-free HTTP console that lets a phone drive the whole
stack/process layer — status, logs, lifecycle, redeploy — when the frontend
build is corrupt or the backend has crashed. It lives in the supervisor
(waypointd), not in either managed service, so it survives a broken frontend
*and* a broken backend.

It is deliberately a *stack* console, not a reimplementation of the app's
session UI: its blast radius is exactly what `waypointctl` can already do.
The action set is a fixed allowlist; there is no arbitrary command execution.

The console page is assembled from the source files in `control_assets/` and
inlined into one self-contained document, so the daemon can serve it without a
static-file route — see `_render_page`.
"""

import hmac
import json
import secrets
import threading
import time
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
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
        # Per-service lanes so frontend and backend can run at once; an "all"
        # op (or a redeploy) holds both. status/logs stay lock-free.
        self._lane_locks = {
            "backend": threading.Lock(),
            "frontend": threading.Lock(),
        }
        self._ops_lock = threading.Lock()
        self._op_threads: list[threading.Thread] = []
        self.ops: dict[str, dict[str, object]] = {}
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
            now = time.monotonic()
            # Drop lapsed tokens so a long-lived daemon doesn't accumulate them.
            self._tokens = {t: exp for t, exp in self._tokens.items() if exp > now}
            token = secrets.token_urlsafe(32)
            self._tokens[token] = now + TOKEN_TTL_SECONDS
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
    def _lanes_for(self, key: str) -> tuple[str, ...]:
        return ("backend", "frontend") if key == "all" else (key,)

    def start_op(
        self,
        key: str,
        action: str,
        target: str,
        run: Callable[[commands.LogFn], ServiceResult],
    ) -> bool:
        """Kick off a mutating op on a worker thread, keyed by service lane.

        Returns False if a conflicting op already holds a lane this key needs.
        `backend` and `frontend` are independent lanes; `all` (and redeploy)
        holds both, so it can't overlap either.
        """
        lanes = self._lanes_for(key)
        acquired: list[str] = []
        for lane in lanes:
            if self._lane_locks[lane].acquire(blocking=False):
                acquired.append(lane)
            else:
                for held in acquired:
                    self._lane_locks[held].release()
                return False
        self._set_op(key, action, target, "running", "")

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
                # Record the terminal state before releasing the lanes, so a
                # waiting op can't acquire one and flip the record to "running"
                # only to have this worker clobber it with the finished result.
                self._set_op(key, action, target, state, message)
                print(f"[control] {action} {target} -> {state}", flush=True)
                for lane in acquired:
                    self._lane_locks[lane].release()

        thread = threading.Thread(target=worker, name=f"control-{key}", daemon=True)
        with self._ops_lock:
            self._op_threads = [t for t in self._op_threads if t.is_alive()]
            self._op_threads.append(thread)
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
        with self._ops_lock:
            threads = list(self._op_threads)
        for thread in threads:
            thread.join(timeout=timeout)

    def ops_snapshot(self) -> list[dict[str, object]]:
        with self._ops_lock:
            return list(self.ops.values())

    def _set_op(
        self, key: str, action: str, target: str, state: str, message: str
    ) -> None:
        with self._ops_lock:
            self.ops[key] = {
                "key": key,
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
                "ops": self.control.ops_snapshot(),
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
            target,
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
            "all",
            "redeploy",
            channel,
            lambda log: self.control.redeploy(channel, log),
        )
        self._respond_started(started, "redeploy", channel)

    def _respond_started(self, started: bool, action: str, target: str) -> None:
        if not started:
            self._send_json(
                HTTPStatus.CONFLICT,
                {"error": "a conflicting operation is already running"},
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


def _render_page() -> str:
    """Inline the control_assets sources into one self-contained document."""
    assets = files(__package__).joinpath("control_assets")
    html = assets.joinpath("control.html").read_text(encoding="utf-8")
    css = assets.joinpath("control.css").read_text(encoding="utf-8")
    js = assets.joinpath("control.js").read_text(encoding="utf-8")
    return html.replace("/*__CONTROL_CSS__*/", css).replace("//__CONTROL_JS__", js)


_PAGE = _render_page()

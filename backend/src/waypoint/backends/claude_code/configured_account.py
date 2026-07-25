"""Identify a Claude config dir that declares its own auth in ``settings.json``.

A config dir does not have to hold an OAuth login. Writing an ``env`` block into
``<config_dir>/settings.json`` — typically a bearer token, often against a custom
``ANTHROPIC_BASE_URL`` — is a supported way to point one at a gateway or a
separate credential, and the CLI applies that block over the inherited process
env, so it is what the agent actually authenticates as.

Such a dir has no OAuth credentials and no ``oauthAccount``, so the rate-limit
probe that normally identifies an account returns nothing and the runtime refuses
to switch a session onto it. This module supplies the missing identity from the
dir's own configuration instead: deterministic, local, and free of network calls.

Auth is read from **that dir's** ``settings.json`` only. It is the sole layer
that is per-profile, and therefore the only one that can distinguish one account
from another: a token in ``plugin_configs.<agent>.env`` reaches every session of
every profile, so deriving identity from it would collapse them all onto one key
and make the runtime's "this switch would not change the account" guard refuse
every switch. The endpoint is the one exception — see :func:`_resolve_base_url`.
"""

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from waypoint.schemas import AccountProbeResult

DEFAULT_ANTHROPIC_HOST = "api.anthropic.com"

_BASE_URL_VAR = "ANTHROPIC_BASE_URL"

# Env vars whose presence in a profile's own settings.json means "this dir
# authenticates as this secret, not as an OAuth login". Both sit ahead of stored
# OAuth credentials in the CLI's credential chain unconditionally, so an identity
# derived from either matches the process on every transport.
#
# ANTHROPIC_API_KEY is deliberately absent: the CLI gates the env api key on
# *interactivity*, not on the endpoint — a headless ``--print`` run uses it, while
# the interactive TUI uses it only once its hash is recorded in .claude.json's
# ``customApiKeyResponses.approved`` and otherwise falls back to OAuth. An
# identity that cannot know the transport would be right for one and wrong for
# the other, so the api key only ever contributes to the digest below.
_TRIGGER_SECRET_VARS = ("ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN")

# Everything that can differentiate two dirs pointed at the same endpoint. Wider
# than the trigger set on purpose: a var that is not digested cannot distinguish
# accounts, so two profiles differing only in it would collapse onto one key.
_DIGESTED_VARS = (
    *_TRIGGER_SECRET_VARS,
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_CUSTOM_HEADERS",
)

_DIGEST_CHARS = 12


def settings_file(config_dir: str | None) -> Path:
    """The user-layer settings file for ``config_dir`` (else the default home).

    ``<config_dir>/settings.json`` when a profile is active, else
    ``~/.claude/settings.json``. There is no user-layer ``settings.local.json``:
    the CLI reads that name only for project settings.
    """
    home = Path(config_dir).expanduser() if config_dir else Path.home() / ".claude"
    return home / "settings.json"


def read_settings_env(config_dir: str | None) -> dict[str, str]:
    """The ``env`` block of ``config_dir``'s settings file, string entries only.

    Degrades to ``{}`` on a missing, unreadable, or malformed file and on a
    non-object ``env`` — a broken settings file must leave the caller falling
    back to the live probe, never raise into a launch or a switch.
    """
    path = settings_file(config_dir)
    try:
        if not path.is_file():
            return {}
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    env = data.get("env")
    if not isinstance(env, dict):
        return {}
    return {
        str(key): value
        for key, value in env.items()
        if isinstance(value, str) and value
    }


def _present(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name, "").strip()
    return value or None


def _resolve_base_url(
    settings_env: Mapping[str, str], launch_env: Mapping[str, str]
) -> str | None:
    """The endpoint this dir talks to: launch env, overridden by settings.

    The only value not confined to the settings layer. A dir whose settings file
    carries just the token while the endpoint comes from a plugin-level ``env``
    would otherwise be keyed and *labelled* ``api.anthropic.com`` while actually
    talking to a gateway. Reading it cannot collapse profiles the way reading a
    secret would: an endpoint alone never yields an identity, and a shared one
    contributes identical material to every profile, leaving the per-profile
    secret digest to discriminate. Settings wins, matching the CLI.
    """
    return _present(settings_env, _BASE_URL_VAR) or _present(launch_env, _BASE_URL_VAR)


def _endpoint_host(base_url: str | None) -> str:
    """``hostname[:port]`` of ``base_url``, lowercased and userinfo-stripped.

    Built from ``hostname``/``port`` rather than ``netloc`` because this string is
    plaintext in both the account key and the human-facing label: a gateway URL
    of the form ``https://user:pw@host/v1`` has a ``netloc`` that carries the
    password. ``hostname`` also normalises case, so one endpoint cannot mint two
    keys. An absent or unparsable URL resolves to the default host.
    """
    if not base_url:
        return DEFAULT_ANTHROPIC_HOST
    try:
        parts = urlsplit(base_url)
        host = parts.hostname
        port = parts.port
    except ValueError:
        return DEFAULT_ANTHROPIC_HOST
    if not host:
        return DEFAULT_ANTHROPIC_HOST
    return f"{host}:{port}" if port else host


def _normalized_base_url(base_url: str | None) -> str:
    """A comparable form of ``base_url`` for the digest, without userinfo.

    Scheme and path are digested even though only the host is shown: two
    endpoints that share a host still differ — ``http://`` vs ``https://``, or a
    path-routed ``https://gw/v1/team-a`` vs ``…/team-b`` — and folding them in
    keeps two such profiles from resolving to the same account.
    """
    if not base_url:
        return ""
    try:
        parts = urlsplit(base_url)
    except ValueError:
        return base_url.strip().rstrip("/")
    host = _endpoint_host(base_url)
    path = parts.path.rstrip("/")
    return f"{parts.scheme.lower()}://{host}{path}"


def _digest(settings_env: Mapping[str, str], base_url: str | None) -> str:
    material = [f"{_BASE_URL_VAR}={_normalized_base_url(base_url)}"]
    material.extend(
        f"{name}={value}"
        for name in sorted(_DIGESTED_VARS)
        if (value := _present(settings_env, name)) is not None
    )
    return hashlib.sha256("\n".join(material).encode("utf-8")).hexdigest()[
        :_DIGEST_CHARS
    ]


def configured_account_identity(
    config_dir: str | None, launch_env: Mapping[str, str]
) -> AccountProbeResult | None:
    """The account ``config_dir`` authenticates as by its own configuration.

    Returns an identity only when the dir's ``settings.json`` declares a bearer
    secret (see ``_TRIGGER_SECRET_VARS``); ``None`` otherwise, which leaves the
    caller on the live OAuth probe. Requiring a declared secret is what keeps
    this from re-identifying a dir that really does authenticate as its OAuth
    account: a custom endpoint alone does not imply a different account (a
    gateway can proxy an OAuth login), and flipping such a profile's key would
    break a configured ``expected_account_key`` that matches today.

    The key carries the endpoint host and a digest, never a secret; both it and
    the label reach API payloads, and the label is not redacted.
    """
    settings_env = read_settings_env(config_dir)
    if not any(_present(settings_env, name) for name in _TRIGGER_SECRET_VARS):
        return None
    base_url = _resolve_base_url(settings_env, launch_env)
    host = _endpoint_host(base_url)
    return AccountProbeResult(
        account_key=f"claude_code:endpoint:{host}:{_digest(settings_env, base_url)}",
        account_label=f"{host} · token auth",
        source="api",
    )

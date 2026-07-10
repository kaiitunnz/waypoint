"""Remote filesystem helper for RemoteTranscriptFilesystem.

Designed to be piped to ``python3 -`` over SSH on a remote launch target
(model: ``claude_code/remote_probe_script.py``). The operation and its
arguments travel as trailing argv after the ``-`` (stdin carries only this
script's own source), since stdin is already spent on the script body. Prints
one sentinel-framed JSON line to stdout describing the result.

Stdlib-only; targets Python 3.8+ so it runs on most pre-installed remote
interpreters without a virtualenv.

Output schema (always one line, ``SENTINEL`` immediately followed by JSON,
``\\n``-terminated) — query ops:

    {"exists": bool}
    {"is_dir": bool}
    {"is_symlink": bool}
    {"target": str} | {"error": str}            # readlink
    {"entries": [str, ...]} | {"error": str}    # listdir
    {"paths": [str, ...]}                       # glob
    {"size": int, "device": int, "inode": int,  # read_range
     "data_b64": str} | {"error": str}

and mutating ops (mkdir, chmod, rmdir, symlink, copy_file):

    {"ok": true} | {"error": str}
"""

# Deferred annotation evaluation lets this file carry modern-syntax type hints
# (for mypy, which runs on the dev machine) while never evaluating them on the
# remote Python 3.8+ interpreter that actually executes this script.
from __future__ import annotations

import base64
import glob as _glob
import json
import os
import shutil
import stat as _stat
import sys
from collections.abc import Callable

SENTINEL = "__WP_FS_BEGIN__"


def emit(payload):
    sys.stdout.write(SENTINEL)
    sys.stdout.write(json.dumps(payload))
    sys.stdout.write("\n")
    sys.stdout.flush()


def _u(path):
    # Expand a leading ``~`` against the remote home. Every path arg is run
    # through this so a ``~``-relative config_dir/shared dir resolves on the
    # remote host, not the backend host that built the command.
    return os.path.expanduser(path)


def op_exists(path):
    emit({"exists": os.path.exists(_u(path))})


def op_is_dir(path):
    emit({"is_dir": os.path.isdir(_u(path))})


def op_is_symlink(path):
    emit({"is_symlink": os.path.islink(_u(path))})


def op_readlink(path):
    try:
        emit({"target": os.readlink(_u(path))})
    except OSError as exc:
        emit({"error": str(exc)})


def op_listdir(path):
    try:
        emit({"entries": os.listdir(_u(path))})
    except OSError as exc:
        emit({"error": str(exc)})


def op_mkdir(path, parents, exist_ok):
    path = _u(path)
    want_parents = parents == "1"
    want_exist_ok = exist_ok == "1"
    try:
        if want_parents:
            os.makedirs(path, exist_ok=want_exist_ok)
        else:
            os.mkdir(path)
    except FileExistsError:
        if want_exist_ok:
            emit({"ok": True})
        else:
            emit({"error": "path exists: " + path})
        return
    except OSError as exc:
        emit({"error": str(exc)})
        return
    emit({"ok": True})


def op_chmod(path, mode):
    try:
        os.chmod(_u(path), int(mode))
    except OSError as exc:
        emit({"error": str(exc)})
        return
    emit({"ok": True})


def op_rmdir(path):
    try:
        os.rmdir(_u(path))
    except OSError as exc:
        emit({"error": str(exc)})
        return
    emit({"ok": True})


def op_symlink(path, target):
    try:
        os.symlink(_u(target), _u(path))
    except OSError as exc:
        emit({"error": str(exc)})
        return
    emit({"ok": True})


def op_copy_file(src, dst, mode):
    try:
        shutil.copy2(_u(src), _u(dst))
        os.chmod(_u(dst), int(mode))
    except OSError as exc:
        emit({"error": str(exc)})
        return
    emit({"ok": True})


def op_glob(config_dir, pattern):
    root = os.path.expanduser(config_dir)
    matches = sorted(_glob.glob(os.path.join(root, pattern)))
    emit({"paths": matches})


def op_read_range(path, offset, limit):
    # Bounded, read-only tail: seek to ``offset`` and read at most ``limit``
    # bytes of a regular file, returning the file's size and stable identity
    # (device, inode) so the caller can detect truncation/replacement. Bytes
    # travel only inside ``data_b64`` — nothing raw is ever printed.
    p = _u(path)
    try:
        want_offset = max(0, int(offset))
        want_limit = max(0, int(limit))
    except (TypeError, ValueError):
        emit({"error": "offset and limit must be integers"})
        return
    try:
        st = os.stat(p)
        if not _stat.S_ISREG(st.st_mode):
            emit({"error": "not a regular file: " + p})
            return
        if want_limit == 0:
            data = b""
        else:
            with open(p, "rb") as fh:
                fh.seek(want_offset)
                data = fh.read(want_limit)
    except OSError as exc:
        emit({"error": str(exc)})
        return
    emit(
        {
            "size": st.st_size,
            "device": st.st_dev,
            "inode": st.st_ino,
            "data_b64": base64.b64encode(data).decode("ascii"),
        }
    )


def op_expanduser(path):
    # Resolve ``~`` against the remote home so the caller's policy logic
    # (relative_to, symlink-target comparison) works with absolute paths that
    # match what glob/symlink produce on this host.
    emit({"path": os.path.expanduser(path)})


OPS: dict[str, Callable[..., None]] = {
    "exists": op_exists,
    "is_dir": op_is_dir,
    "is_symlink": op_is_symlink,
    "readlink": op_readlink,
    "listdir": op_listdir,
    "mkdir": op_mkdir,
    "chmod": op_chmod,
    "rmdir": op_rmdir,
    "symlink": op_symlink,
    "copy_file": op_copy_file,
    "glob": op_glob,
    "read_range": op_read_range,
    "expanduser": op_expanduser,
}


def main():
    if len(sys.argv) < 2:
        emit({"error": "missing op"})
        return
    op, args = sys.argv[1], sys.argv[2:]
    handler = OPS.get(op)
    if handler is None:
        emit({"error": "unknown op: " + op})
        return
    try:
        handler(*args)
    except TypeError:
        emit({"error": "wrong number of arguments for op: " + op})


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        emit({"error": "internal: " + repr(exc)})

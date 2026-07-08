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

and mutating ops (mkdir, chmod, rmdir, symlink, copy_file):

    {"ok": true} | {"error": str}
"""

# Deferred annotation evaluation lets this file carry modern-syntax type hints
# (for mypy, which runs on the dev machine) while never evaluating them on the
# remote Python 3.8+ interpreter that actually executes this script.
from __future__ import annotations

import glob as _glob
import json
import os
import shutil
import sys
from collections.abc import Callable

SENTINEL = "__WP_FS_BEGIN__"


def emit(payload):
    sys.stdout.write(SENTINEL)
    sys.stdout.write(json.dumps(payload))
    sys.stdout.write("\n")
    sys.stdout.flush()


def op_exists(path):
    emit({"exists": os.path.exists(path)})


def op_is_dir(path):
    emit({"is_dir": os.path.isdir(path)})


def op_is_symlink(path):
    emit({"is_symlink": os.path.islink(path)})


def op_readlink(path):
    try:
        emit({"target": os.readlink(path)})
    except OSError as exc:
        emit({"error": str(exc)})


def op_listdir(path):
    try:
        emit({"entries": os.listdir(path)})
    except OSError as exc:
        emit({"error": str(exc)})


def op_mkdir(path, parents, exist_ok):
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
        os.chmod(path, int(mode))
    except OSError as exc:
        emit({"error": str(exc)})
        return
    emit({"ok": True})


def op_rmdir(path):
    try:
        os.rmdir(path)
    except OSError as exc:
        emit({"error": str(exc)})
        return
    emit({"ok": True})


def op_symlink(path, target):
    try:
        os.symlink(target, path)
    except OSError as exc:
        emit({"error": str(exc)})
        return
    emit({"ok": True})


def op_copy_file(src, dst, mode):
    try:
        shutil.copy2(src, dst)
        os.chmod(dst, int(mode))
    except OSError as exc:
        emit({"error": str(exc)})
        return
    emit({"ok": True})


def op_glob(config_dir, pattern):
    root = os.path.expanduser(config_dir)
    matches = sorted(_glob.glob(os.path.join(root, pattern)))
    emit({"paths": matches})


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

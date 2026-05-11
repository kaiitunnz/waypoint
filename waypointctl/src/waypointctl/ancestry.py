import subprocess


def is_descendant_of(pid: int, ancestor: int) -> bool:
    current = pid
    seen: set[int] = set()
    while current and current != 1 and current not in seen:
        seen.add(current)
        parent = parent_pid(current)
        if parent is None:
            return False
        if parent == ancestor:
            return True
        current = parent
    return False


def parent_pid(pid: int) -> int | None:
    try:
        result = subprocess.run(
            ["ps", "-o", "ppid=", "-p", str(pid)],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    raw = result.stdout.strip()
    if not raw or not raw.lstrip("-").isdigit():
        return None
    value = int(raw)
    return value if value > 0 else None

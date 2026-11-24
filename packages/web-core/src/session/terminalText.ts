// Match a Waypoint control command (e.g. ``/new``) and return its trailing
// argument string, or null when the text isn't that command. The trailing
// space requirement keeps ``/news`` from being read as ``/new``.
export function matchControlCommand(text: string, name: string): string | null {
  const command = `/${name}`;
  if (text === command) return "";
  if (text.startsWith(`${command} `)) return text.slice(command.length + 1).trim();
  return null;
}

export function formatCwdSegments(cwd: string): string[] {
  if (!cwd) return [""];
  // Render the path as breadcrumb-style segments, but keep the leading slash
  // (or `~`) attached to the first segment so root paths still read correctly.
  const trimmed = cwd.replace(/\/+$/, "");
  const segments = trimmed.split("/").filter(Boolean);
  if (trimmed.startsWith("/")) {
    return segments.length ? [`/${segments[0]}`, ...segments.slice(1)] : ["/"];
  }
  return segments.length ? segments : [trimmed];
}

// Bracketed-paste wraps the payload so TUIs that have enabled mode
// 2004 treat the bytes as a single paste event rather than a stream of
// keystrokes. Normalize ``\r\n`` and lone ``\r`` to ``\n`` first so a
// stray CR can't fire a mid-paste submit on the way in. Wrapped CLIs
// (CC, Codex, OpenCode) all enable mode 2004; raw shells will show the
// literal escape codes, which is the documented trade-off.
export function wrapBracketedPaste(text: string): string {
  return `\x1b[200~${text.replace(/\r\n?/g, "\n")}\x1b[201~`;
}

export function stripAnsi(text: string): string {
  return text
    .replace(/\u001B\][\s\S]*?(?:\u0007|\u001B\\)/g, "")
    .replace(/\u001B\[[0-?]*[ -/]*[@-~]/g, "")
    .replace(/\u001B[@-_]/g, "");
}

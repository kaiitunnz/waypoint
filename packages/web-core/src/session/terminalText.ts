// Bracketed-paste wraps the payload so TUIs that have enabled mode
// 2004 treat the bytes as a single paste event rather than a stream of
// keystrokes. Normalize ``\r\n`` and lone ``\r`` to ``\n`` first so a
// stray CR can't fire a mid-paste submit on the way in. Wrapped CLIs
// (CC, Codex, OpenCode) all enable mode 2004; raw shells echo the escape
// codes.
export function wrapBracketedPaste(text: string): string {
  return `\x1b[200~${text.replace(/\r\n?/g, "\n")}\x1b[201~`;
}

export function stripAnsi(text: string): string {
  return text
    .replace(/\u001B\][\s\S]*?(?:\u0007|\u001B\\)/g, "")
    .replace(/\u001B\[[0-?]*[ -/]*[@-~]/g, "")
    .replace(/\u001B[@-_]/g, "");
}

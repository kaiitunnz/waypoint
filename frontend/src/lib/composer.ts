// Shared between the chat ``ReplyComposer`` and the terminal quick-compose
// drawer so both surfaces honor the same minimum height and platform
// shortcut label.

export const COMPOSER_MIN_HEIGHT = 56;

export const SHORTCUT_IS_MAC =
  typeof navigator !== "undefined" &&
  /Mac|iPhone|iPad|iPod/.test(navigator.platform || navigator.userAgent || "");

// Outcome of a terminal quick-compose submit. ``ok`` covers both a WebSocket
// send and a successfully handled control command (e.g. ``/new``) — the draft
// clears either way. ``socket-closed`` keeps the draft and prompts a retry.
// ``command-error`` keeps the draft but stays silent because the control
// command already surfaced its own error.
export type TerminalSubmitResult = "ok" | "socket-closed" | "command-error";

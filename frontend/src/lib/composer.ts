// Shared between the chat ``ReplyComposer`` and the terminal quick-compose
// drawer so both surfaces honor the same minimum height and platform
// shortcut label.

export const COMPOSER_MIN_HEIGHT = 56;

export const SHORTCUT_IS_MAC =
  typeof navigator !== "undefined" &&
  /Mac|iPhone|iPad|iPod/.test(navigator.platform || navigator.userAgent || "");

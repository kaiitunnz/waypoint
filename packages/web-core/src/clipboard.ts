// Clipboard helper that survives plain-HTTP origins and Safari. The async
// Clipboard API (`navigator.clipboard`) is unavailable in non-secure contexts
// (Waypoint is reached over http on a tailnet) and Safari rejects it outside a
// trusted gesture, so fall back to the legacy `execCommand("copy")` path.

function legacyCopy(text: string): boolean {
  const el = document.createElement("textarea");
  el.value = text;
  el.setAttribute("readonly", "");
  el.style.position = "fixed";
  el.style.opacity = "0";
  document.body.appendChild(el);
  el.select();
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  }
  document.body.removeChild(el);
  return ok;
}

export async function copyText(text: string): Promise<boolean> {
  if (!text) return false;
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // writeText can reject at runtime when the document loses focus or the
      // user denied clipboard-write permission. Fall back before giving up.
      return legacyCopy(text);
    }
  }
  return legacyCopy(text);
}

// iOS Safari rejects ``navigator.clipboard.writeText`` even from inside a
// click handler — the Promise microtask ends the transient activation
// window before the write resolves. ``document.execCommand("copy")`` is
// deprecated everywhere but remains the one synchronous clipboard write
// that survives that gauntlet across iOS Safari, desktop Safari, and
// modern Chromium.
//
// A contenteditable ``<div>`` is the right host: textarea's ``.value``
// lives in an internal buffer (not DOM children), so
// ``Range.selectNodeContents(textarea)`` selects nothing — execCommand
// then reports success against an empty selection and the paste is
// blank. The div holds the text in real text nodes; ``white-space: pre``
// preserves newlines so multi-line ``/copy`` results survive intact.
export function copyTextSync(text: string): boolean {
  if (typeof document === "undefined") return false;
  const host = document.createElement("div");
  host.textContent = text;
  host.setAttribute("contenteditable", "true");
  // ``left: -9999px`` keeps the node selectable but visually off-canvas
  // (opacity 0 in the same coordinate space can still flash a focus
  // ring on iOS). ``font-size: 16px`` prevents iOS's auto-zoom on
  // focused editable fields. ``white-space: pre`` carries newlines.
  host.style.cssText = [
    "position:fixed",
    "top:0",
    "left:-9999px",
    "width:1px",
    "height:1px",
    "white-space:pre",
    "font-size:16px",
    "user-select:text",
    "-webkit-user-select:text",
  ].join(";");
  document.body.appendChild(host);
  let ok = false;
  const sel = window.getSelection();
  try {
    const range = document.createRange();
    range.selectNodeContents(host);
    if (sel) {
      sel.removeAllRanges();
      sel.addRange(range);
    }
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  } finally {
    sel?.removeAllRanges();
    document.body.removeChild(host);
  }
  return ok;
}

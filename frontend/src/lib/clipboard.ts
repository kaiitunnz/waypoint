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

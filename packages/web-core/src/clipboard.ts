// The async Clipboard API is missing on plain-HTTP origins (Waypoint is reached
// over http on a tailnet), and iOS Safari rejects it once a gesture's activation
// has passed. `execCommand("copy")` is deprecated but synchronous, so it still
// works inside the tap that calls it.

export async function copyText(text: string): Promise<boolean> {
  if (!text) return false;
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      return copyTextSync(text);
    }
  }
  return copyTextSync(text);
}

// Copies inside the current gesture. The text sits in a contenteditable element:
// a range cannot select a textarea's value, and `white-space: pre` keeps newlines.
export function copyTextSync(text: string): boolean {
  if (typeof document === "undefined") return false;
  const host = document.createElement("div");
  host.textContent = text;
  host.setAttribute("contenteditable", "true");
  // Off-canvas rather than transparent, which can flash a focus ring on iOS; 16px
  // keeps iOS from zooming to it.
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
  // Selecting DOM text would also copy an HTML flavour with its computed styles.
  host.addEventListener("copy", (event) => {
    event.preventDefault();
    event.clipboardData?.setData("text/plain", text);
  });
  document.body.appendChild(host);
  const selection = window.getSelection();
  let ok = false;
  try {
    const range = document.createRange();
    range.selectNodeContents(host);
    selection?.removeAllRanges();
    selection?.addRange(range);
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  } finally {
    selection?.removeAllRanges();
    document.body.removeChild(host);
  }
  return ok;
}

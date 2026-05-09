import { useCallback, useState } from "react";

function legacyCopy(text: string): boolean {
  // execCommand("copy") has different security gating than the async API:
  // it works on plain-HTTP origins and when document focus is ambiguous,
  // as long as the call originates from a user gesture. It returns false
  // (rather than throwing) when the host browser refuses.
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

export function CopyMessageButton({
  text,
  label = "Copy message",
}: {
  text: string;
  label?: string;
}) {
  const [copied, setCopied] = useState(false);
  const onCopy = useCallback(async () => {
    if (!text) return;
    let ok = false;
    if (navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(text);
        ok = true;
      } catch {
        // writeText can reject at runtime when the document loses focus or
        // the user denied clipboard-write permission. Fall back before giving
        // up so the button still works in plain-HTTP development contexts.
        ok = legacyCopy(text);
      }
    } else {
      ok = legacyCopy(text);
    }
    if (!ok) return;
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  }, [text]);
  return (
    <button
      type="button"
      className={`message-copy${copied ? " copied" : ""}`}
      onClick={(event) => {
        event.stopPropagation();
        event.preventDefault();
        void onCopy();
      }}
      aria-label={copied ? "Copied" : label}
      title={copied ? "Copied" : label}
    >
      <span aria-hidden>{copied ? "✓" : "⎘"}</span>
    </button>
  );
}

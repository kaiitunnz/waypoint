import { useCallback, useState } from "react";

import { copyText } from "@/lib/clipboard";

export function CopyMessageButton({
  text,
  label = "Copy message",
}: {
  text: string;
  label?: string;
}) {
  const [copied, setCopied] = useState(false);
  const onCopy = useCallback(async () => {
    const ok = await copyText(text);
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

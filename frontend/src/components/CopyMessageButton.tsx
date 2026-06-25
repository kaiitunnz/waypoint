import { useCallback } from "react";

import { copyText } from "@/lib/clipboard";
import { useCopied } from "@/lib/use-copied";

export function CopyMessageButton({
  text,
  label = "Copy message",
}: {
  text: string;
  label?: string;
}) {
  const { copied, markCopied } = useCopied();
  const onCopy = useCallback(async () => {
    if (await copyText(text)) markCopied();
  }, [text, markCopied]);
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

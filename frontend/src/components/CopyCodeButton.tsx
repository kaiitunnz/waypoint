import { useCallback } from "react";

import { copyText } from "@/lib/clipboard";
import { useCopied } from "@/lib/use-copied";

export function CopyCodeButton({ text }: { text: string }) {
  const { copied, markCopied, reset } = useCopied();
  const onCopy = useCallback(async () => {
    if (await copyText(text)) markCopied();
  }, [text, markCopied]);
  const onMouseLeave = useCallback(() => {
    // Desktop: revert the confirmation the moment the pointer leaves so a
    // re-hover shows the copy glyph, not a stale check. Touch has no hover,
    // so the timeout handles the reset there.
    if (window.matchMedia("(hover: hover)").matches) reset();
  }, [reset]);
  return (
    <button
      type="button"
      className={`code-copy${copied ? " copied" : ""}`}
      onClick={(event) => {
        event.stopPropagation();
        event.preventDefault();
        void onCopy();
      }}
      onMouseLeave={onMouseLeave}
      aria-label={copied ? "Copied" : "Copy code"}
      title={copied ? "Copied" : "Copy code"}
    >
      <span aria-hidden>{copied ? "✓" : "⎘"}</span>
    </button>
  );
}

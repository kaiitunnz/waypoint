"use client";

import { useCallback, useState } from "react";

import { pasteNeedsConfirmation } from "../session/terminal";
import { wrapBracketedPaste } from "../session/terminalText";

export interface TerminalPaste {
  // Reads the clipboard and pastes it into the pane, or stages it for a tap.
  request: () => Promise<void>;
  // The staged text, on browsers that ask before every clipboard read.
  pending: string | null;
  // Bumped per staged paste, so a prompt can replay its entrance.
  seq: number;
  sendPending: () => void;
  dismiss: () => void;
}

// The key bar's Paste. WebKit shows a system "Paste" banner for each read, so
// there the text waits behind a second tap that shows its length.
export function useTerminalPaste(send: (data: string) => void, onError: (message: string) => void): TerminalPaste {
  const [pending, setPending] = useState<string | null>(null);
  const [seq, setSeq] = useState(0);

  const request = useCallback(async () => {
    if (typeof navigator === "undefined" || !navigator.clipboard?.readText) {
      onError("Clipboard read is unavailable in this browser");
      return;
    }
    let text: string;
    try {
      text = await navigator.clipboard.readText();
    } catch {
      onError("Clipboard access blocked — approve the paste prompt and retry");
      return;
    }
    if (!text) return;
    if (pasteNeedsConfirmation()) {
      setPending(text);
      setSeq((value) => value + 1);
    } else {
      send(wrapBracketedPaste(text));
    }
  }, [send, onError]);

  const sendPending = useCallback(() => {
    if (pending === null) return;
    send(wrapBracketedPaste(pending));
    setPending(null);
  }, [pending, send]);

  const dismiss = useCallback(() => setPending(null), []);

  return { request, pending, seq, sendPending, dismiss };
}

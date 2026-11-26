"use client";

import { useCallback } from "react";

import { useTerminalPaste } from "@waypoint/web-core/hooks/useTerminalPaste";

import { useToast } from "@/components/ui/Toast";

import { PastePrompt } from "./PastePrompt";
import { TerminalKeyBar } from "./TerminalKeyBar";

// The key bar, with the paste it reads from the clipboard.
export function TerminalKeys({ input }: { input: (data: string) => void }) {
  const { toast } = useToast();
  const reportError = useCallback((message: string) => toast({ message, tone: "error" }), [toast]);
  const paste = useTerminalPaste(input, reportError);
  return (
    <>
      {paste.pending !== null ? (
        <PastePrompt key={paste.seq} length={paste.pending.length} onSend={paste.sendPending} onDismiss={paste.dismiss} />
      ) : null}
      <TerminalKeyBar onSend={input} onPaste={() => void paste.request()} />
    </>
  );
}

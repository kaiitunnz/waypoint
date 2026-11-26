"use client";

import { useEffect, useRef } from "react";

import { copyTextSync } from "@waypoint/web-core/clipboard";
import { useSessionModels } from "@waypoint/web-core/hooks/useSessionModels";
import { modelLabelFor } from "@waypoint/web-core/modelDisplay";
import type { ModelNotice } from "@waypoint/web-core/session/modelNotice";
import type { SessionRecord } from "@waypoint/web-core/types";

import { useToast } from "@/components/ui/Toast";
import { handleAuthFailure } from "@/lib/auth";

import styles from "./SessionNotices.module.css";

const MODEL_KEY = "session-model";
const CLIPBOARD_KEY = "session-clipboard";
const COPIED_MS = 1800;

// Mounted only while its toast shows, so the model list loads only for a notice.
function ModelNoticeText({ notice, host, token, session }: { notice: ModelNotice; host: string; token: string; session: SessionRecord | null }) {
  const { modelOptions } = useSessionModels(host, token, session, handleAuthFailure);
  const label = (id: string) => modelLabelFor(id, modelOptions);
  if (notice.reason === "switch") {
    return (
      <>
        <span className={styles.lead}>Model changed</span> {notice.previous ? `${label(notice.previous)} → ` : ""}
        {label(notice.current)}
      </>
    );
  }
  return (
    <>
      <span className={styles.lead}>Running {label(notice.current)}</span>
      {notice.selection ? <span className={styles.muted}> · selected {label(notice.selection)}</span> : null}
    </>
  );
}

interface Options {
  host: string;
  token: string;
  session: SessionRecord | null;
  modelNotice: ModelNotice | null;
  clipboard: { text: string; seq: number } | null;
}

// The session's own toasts: a model change, and a `/copy` result the browser
// would not let the page copy without a tap.
export function useSessionNotices({ host, token, session, modelNotice, clipboard }: Options) {
  const { toast, dismiss } = useToast();
  const sessionRef = useRef(session);
  useEffect(() => {
    sessionRef.current = session;
  }, [session]);

  useEffect(() => {
    if (!modelNotice) {
      dismiss(MODEL_KEY);
      return;
    }
    toast({
      key: MODEL_KEY,
      duration: null,
      message: <ModelNoticeText notice={modelNotice} host={host} token={token} session={sessionRef.current} />,
    });
  }, [modelNotice, host, token, toast, dismiss]);

  useEffect(() => {
    if (!clipboard) return;
    const copied = () => toast({ key: CLIPBOARD_KEY, message: "Copied", duration: COPIED_MS });
    const offer = () =>
      toast({
        key: CLIPBOARD_KEY,
        duration: null,
        message: `Response ready · ${clipboard.text.length.toLocaleString()} chars`,
        action: {
          label: "Copy",
          // Inside the tap, so Safari lets the page write the clipboard.
          onSelect: () => {
            if (!copyTextSync(clipboard.text)) return false;
            copied();
          },
        },
      });
    const writer = navigator.clipboard?.writeText(clipboard.text);
    if (writer) writer.then(copied, offer);
    else offer();
  }, [clipboard, toast]);

  useEffect(
    () => () => {
      dismiss(MODEL_KEY);
      dismiss(CLIPBOARD_KEY);
    },
    [dismiss],
  );
}

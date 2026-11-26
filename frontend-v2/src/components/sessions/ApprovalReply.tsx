"use client";

import { useState, type ChangeEvent, type KeyboardEvent } from "react";

import { isModifiedEnterShortcut } from "@waypoint/web-core/keyboard";
import { decideAsk, decisionView, type DecisionKind } from "@waypoint/web-core/pendingAskActions";
import type { PendingApprovalAsk, PendingPlanAsk } from "@waypoint/web-core/types";

import { Button } from "@/components/ui/Button";

import { useAskResponder } from "./useAskResponder";

const VARIANT = { approve: "primary", session: "text", decline: "danger" } as const satisfies Record<
  DecisionKind,
  string
>;

// An approval decided where it is listed: its reply and the note that goes with it.
export function useApprovalReply(ask: PendingApprovalAsk | PendingPlanAsk) {
  const { actions, supportsNote } = decisionView(ask);
  const [noteOpen, setNoteOpen] = useState(false);
  const [note, setNote] = useState("");
  const { phase, send, busy, dismiss } = useAskResponder(ask.id);
  const disabled = busy || ask.availability !== "actionable";

  const decide = (decision: string) =>
    void send((host, token, retry) => decideAsk(host, token, ask, decision, note, retry), decision);

  const noteProps = {
    value: note,
    disabled,
    "aria-label": "Note",
    placeholder: "Note",
    onChange: (event: ChangeEvent<HTMLInputElement>) => {
      setNote(event.target.value);
      dismiss();
    },
    onKeyDown: (event: KeyboardEvent<HTMLInputElement>) => {
      if (!isModifiedEnterShortcut(event)) return;
      event.preventDefault();
      const approve = actions.find((action) => action.kind === "approve");
      if (approve && !disabled) decide(approve.decision);
    },
  };

  return {
    actions,
    phase,
    disabled,
    decide,
    noteOpen,
    openNote: () => setNoteOpen(true),
    offersNote: supportsNote && !noteOpen,
    noteProps,
  };
}

export function DecisionButtons({ reply }: { reply: ReturnType<typeof useApprovalReply> }) {
  const { actions, phase, disabled, decide } = reply;
  return actions.map((action) => (
    <Button
      key={action.decision}
      variant={VARIANT[action.kind]}
      size="sm"
      disabled={disabled}
      busy={phase.state === "sending" && phase.action === action.decision}
      onClick={() => decide(action.decision)}
    >
      {action.label}
    </Button>
  ));
}

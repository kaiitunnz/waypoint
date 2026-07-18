"use client";

import Link from "next/link";

import { AssistantMark } from "@/components/AssistantMark";
import type { AssistantSummary, SessionStatus } from "@/lib/types";

// Bottom-right cluster of two low-profile glass circles (Inbox above,
// Assistant below), icon-only at rest and expanding to labels on hover/focus.
// The inbox count comes from the shared useInboxCount source; the assistant
// lamp reflects AssistantSummary.status.
interface PresenceFloatersProps {
  assistant: AssistantSummary | null;
  inboxCount: number;
}

function presenceTone(status: SessionStatus): {
  tone: "live" | "idle" | "off";
  word: string;
} {
  switch (status) {
    case "running":
    case "starting":
    case "waiting_input":
      return { tone: "live", word: "active" };
    case "idle":
    case "interrupted":
      return { tone: "idle", word: "idle" };
    default:
      return { tone: "off", word: "offline" };
  }
}

export function PresenceFloaters({ assistant, inboxCount }: PresenceFloatersProps) {
  const capped = inboxCount > 99 ? "99+" : String(inboxCount);
  const presence = assistant ? presenceTone(assistant.status) : null;

  return (
    <div className="presence-cluster">
      <Link
        className="presence-floater presence-inbox"
        href="/inbox"
        aria-label={
          inboxCount > 0 ? `Inbox — ${inboxCount} need you` : "Inbox — nothing open"
        }
      >
        <span className="presence-floater-icon" aria-hidden="true">
          <EnvelopeIcon />
        </span>
        <span className="presence-floater-label">Inbox</span>
        <span
          className={`presence-floater-badge${inboxCount > 0 ? "" : " is-zero"}`}
          aria-hidden="true"
        >
          {capped}
        </span>
      </Link>
      {assistant ? (
        <Link
          className="presence-floater presence-assistant"
          href="/assistant"
          aria-label={`Assistant — ${presence?.word}`}
        >
          <span className="presence-floater-icon" aria-hidden="true">
            <AssistantMark />
          </span>
          <span className="presence-floater-label">Assistant</span>
          <span
            className={`presence-floater-lamp tone-${presence?.tone}`}
            title={presence?.word}
            aria-hidden="true"
          />
        </Link>
      ) : null}
    </div>
  );
}

function EnvelopeIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="17"
      height="17"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <path d="m3 7 9 6 9-6" />
    </svg>
  );
}

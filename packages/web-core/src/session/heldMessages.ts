import type { HeldMessage } from "../types";

export function heldMessageOriginLabel(message: HeldMessage): string {
  if (message.origin === "schedule") return "Scheduled";
  if (message.origin === "wake") return "Wake-up";
  return `From ${message.sender_title || message.sender_session_id || "a session"}`;
}

export function messageText(text: string, attached: number): string {
  const suffix = attached ? ` (+${attached} file${attached === 1 ? "" : "s"})` : "";
  return (text || "(no text)") + suffix;
}

export function heldMessageText(message: HeldMessage): string {
  return messageText(message.text, message.attachments.length);
}

// `manual` marks holds the human controls through Focus rather than the
// session's own state.
export function holdReasonLabel(
  message: HeldMessage,
  focus: boolean,
): { text: string; manual: boolean } {
  if (message.hold_reason === "focus") {
    return { text: "Held by Focus", manual: true };
  }
  if (focus) {
    return { text: "Paused by Focus", manual: true };
  }
  const text =
    message.hold_reason === "dialog"
      ? "Sends after you respond"
      : message.hold_reason === "restore"
        ? "Sends when the session restores"
        : "Sends when idle";
  return { text, manual: false };
}

// A `held_messages` frame's list for this session; null for another session's frame.
export function parseHeldMessagesFrame(payload: unknown, sessionId: string): HeldMessage[] | null {
  if (!payload || typeof payload !== "object") return null;
  const { session_id, held_messages } = payload as { session_id?: unknown; held_messages?: unknown };
  if (session_id !== sessionId) return null;
  return Array.isArray(held_messages) ? (held_messages as HeldMessage[]) : [];
}

import type { HeldMessage } from "../types";

export function heldMessageOriginLabel(message: HeldMessage): string {
  if (message.origin === "schedule") return "Scheduled";
  if (message.origin === "wake") return "Wake-up";
  return `From ${message.sender_title || message.sender_session_id || "a session"}`;
}

export function heldMessageText(message: HeldMessage): string {
  const attached = message.attachments.length;
  const suffix = attached ? ` (+${attached} file${attached === 1 ? "" : "s"})` : "";
  return (message.text || "(no text)") + suffix;
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

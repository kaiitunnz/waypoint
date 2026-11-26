import type { Backend } from "../types";

// Why a tmux target cannot be attached, or null when it can.
export function attachTargetError(target: string): string | null {
  return target.trim() ? null : "Enter a tmux pane.";
}

// The body of `POST /api/sessions/attach-tmux`.
export function attachRequest(target: string, backendHint: Backend, title: string): Record<string, unknown> {
  const trimmedTitle = title.trim();
  return {
    tmux_target: target.trim(),
    backend_hint: backendHint,
    ...(trimmedTitle ? { title: trimmedTitle } : {}),
  };
}

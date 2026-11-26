import { isModelChangeEvent } from "../events";
import type { EventRecord } from "../types";

// A model change the human should see: a mid-session switch, or a launch that
// runs a different model from the one selected.
export interface ModelNotice {
  reason: "switch" | "initial_mismatch";
  previous: string | null;
  current: string;
  selection: string | null;
}

// Read only from live events: a replayed model.change must not raise it again.
export function modelNoticeFromEvent(event: EventRecord): ModelNotice | null {
  if (!isModelChangeEvent(event)) return null;
  const metadata = event.metadata ?? {};
  return {
    reason: metadata.reason === "switch" ? "switch" : "initial_mismatch",
    previous: typeof metadata.previous_model === "string" ? metadata.previous_model : null,
    current: typeof metadata.current_model === "string" ? metadata.current_model : "",
    selection: typeof metadata.selection === "string" ? metadata.selection : null,
  };
}

// The ways a composed message can leave the composer. The two idle modes are
// also the schedule-message modal's "When idle" choices.
export type IdleBatchMode = "with_previous" | "next_cycle";

export interface IdleSendMode {
  value: IdleBatchMode;
  label: string;
  hint: string;
}

export const IDLE_SEND_MODES: readonly IdleSendMode[] = [
  { value: "with_previous", label: "Send when idle", hint: "Joins the next idle batch" },
  { value: "next_cycle", label: "Send next idle cycle", hint: "Waits for the following idle point" },
];

export type SendMode = "now" | IdleBatchMode;

export const SEND_MODES: readonly { value: SendMode; label: string; hint: string }[] = [
  { value: "now", label: "Send now", hint: "Send immediately" },
  ...IDLE_SEND_MODES,
];

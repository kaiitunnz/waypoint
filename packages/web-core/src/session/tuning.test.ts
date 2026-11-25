import { describe, expect, it } from "vitest";

import { buildCatalog } from "../backends";
import type { BackendDescriptor, SessionRecord } from "../types";
import {
  changeInterruptsTurn,
  nextModeId,
  NO_STAGED_PICKS,
  pendingRestartLabel,
  restartFields,
  tuningDraft,
  tuningOptions,
  tuningSummary,
  type TuningInput,
} from "./tuning";

const MODES = [
  { id: "default", label: "Default" },
  { id: "plan", label: "Plan" },
];

const MODELS = [
  { id: "opus", label: "Opus", supported_efforts: ["low", "high"] },
  { id: "haiku", label: "Haiku", supported_efforts: [] },
  { id: "other", label: "Other", supported_efforts: null },
];

const input = (patch: Partial<TuningInput> = {}): TuningInput => ({
  modeOptions: MODES,
  modelOptions: MODELS,
  effortLevels: ["low", "medium", "high"],
  defaultModelId: "opus",
  defaultModelLabel: "Opus",
  currentModel: null,
  currentEffort: null,
  permissionMode: null,
  ...patch,
});

describe("tuningOptions", () => {
  it("gates efforts by the selected or default model", () => {
    expect(tuningOptions(input()).effortOptions).toEqual(["low", "high"]);
    expect(tuningOptions(input({ currentModel: "haiku" })).effortOptions).toEqual([]);
    expect(tuningOptions(input({ currentModel: "other" })).effortOptions).toEqual(["low", "medium", "high"]);
  });

  it("falls back to every listed effort with no model to resolve", () => {
    expect(tuningOptions(input({ defaultModelId: null })).effortOptions).toEqual(["low", "high"]);
  });

  it("names a current effort the options lack", () => {
    expect(tuningOptions(input({ currentEffort: "high" })).customEffort).toBeNull();
    expect(tuningOptions(input({ currentEffort: "turbo" })).customEffort).toBe("turbo");
    expect(tuningOptions(input()).customEffort).toBeNull();
  });

  it("lists a model the catalogue lacks", () => {
    const options = tuningOptions(input({ currentModel: "claude-x" }));
    expect(options.modelEntries[0]).toMatchObject({ id: "claude-x", label: "Custom · claude-x" });
    expect(options.hasModelPicker).toBe(true);
  });

  it("keeps a picker for a set value without options", () => {
    const options = tuningOptions(input({ modelOptions: [], currentModel: null, currentEffort: "high" }));
    expect(options.hasModelPicker).toBe(false);
    expect(options.hasEffortPicker).toBe(true);
  });
});

describe("tuningSummary", () => {
  it("names mode, model and effort", () => {
    const value = input({ permissionMode: "plan", currentModel: "opus", currentEffort: "high" });
    expect(tuningSummary(value, tuningOptions(value))).toEqual(["Plan", "Opus", "High"]);
  });
  it("names the defaults", () => {
    const value = input();
    expect(tuningSummary(value, tuningOptions(value))).toEqual(["Default", "Default (Opus)", "Default"]);
  });
});

describe("nextModeId", () => {
  it("wraps and starts over from an unknown mode", () => {
    expect(nextModeId(MODES, "default")).toBe("plan");
    expect(nextModeId(MODES, "plan")).toBe("default");
    expect(nextModeId(MODES, "zzz")).toBe("default");
    expect(nextModeId([], "default")).toBeNull();
  });
});

describe("tuningDraft", () => {
  const restart = { mode: true, model: true, effort: false };

  it("shows staged picks only where the change restarts", () => {
    const draft = tuningDraft(input(), { mode: "plan", model: null, effort: "high" }, restart);
    expect(draft.shown).toEqual({ mode: "plan", model: "", effort: "" });
    expect(draft.pending).toEqual({ mode: "plan", model: null, effort: null });
    expect(draft.hasPending).toBe(true);
  });

  it("has nothing pending when a pick matches the session", () => {
    const draft = tuningDraft(input({ permissionMode: "plan" }), { ...NO_STAGED_PICKS, mode: "plan" }, restart);
    expect(draft.hasPending).toBe(false);
  });

  it("labels the staged changes", () => {
    const value = input({ currentModel: "opus" });
    const options = tuningOptions(value);
    const draft = tuningDraft(value, { mode: "plan", model: "", effort: null }, restart);
    expect(pendingRestartLabel(draft, MODES, options.modelEntries)).toBe("Plan mode and the default model");
  });
});

const descriptor = (settings: Record<string, boolean>): BackendDescriptor =>
  ({
    id: "claude_code",
    transport_id: "claude_tty",
    supported_transports: ["claude_tty"],
    agent_capabilities: {},
    transport_capabilities: {
      supports_set_permission_mode_with_restart: true,
      supports_set_model_with_restart: false,
    },
    capabilities: settings,
  }) as unknown as BackendDescriptor;

const session = (patch: Partial<SessionRecord> = {}) =>
  ({ backend: "claude_code", transport: "claude_tty", status: "idle", ...patch }) as SessionRecord;

describe("catalogue flags", () => {
  const catalog = buildCatalog([descriptor({ supports_set_effort_with_restart: true, settings_change_interrupts_turn: true })]);

  it("reads restart flags from the pair and the agent", () => {
    expect(restartFields(session(), catalog)).toEqual({ mode: true, model: false, effort: true });
    expect(restartFields(null, catalog)).toEqual({ mode: false, model: false, effort: false });
  });

  it("asks before interrupting a running turn only", () => {
    expect(changeInterruptsTurn(session({ status: "running" }), catalog)).toBe(true);
    expect(changeInterruptsTurn(session({ status: "waiting_input" }), catalog)).toBe(true);
    expect(changeInterruptsTurn(session(), catalog)).toBe(false);
  });
});

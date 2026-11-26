import { describe, expect, it } from "vitest";

import { ApiError, AuthError, CWD_NOT_FOUND_DETAIL, SSH_MASTER_REQUIRED_DETAIL } from "../api";
import { buildCatalog } from "../backends";
import { defaultRecurrenceState } from "../recurrence";
import { launchCatalogDescriptors } from "../test-helpers";
import { attachRequest, attachTargetError } from "./attach";
import { folderOptions, isCompletable } from "./folders";
import { launchDefaults, threadDiscoveryBackends } from "./launchDefaults";
import { classifyLaunchFailure } from "./launchError";
import { defaultModelLabel, modelChoices } from "./models";
import { withScheduleTiming } from "./schedule";

describe("attach", () => {
  it("needs a pane and sends a title only when given", () => {
    expect(attachTargetError("  ")).toBe("Enter a tmux pane.");
    expect(attachTargetError("main:0.0")).toBeNull();
    expect(attachRequest(" main:0.0 ", "codex", " ")).toEqual({ tmux_target: "main:0.0", backend_hint: "codex" });
    expect(attachRequest("a", "codex", " T ")).toMatchObject({ title: "T" });
  });
});

describe("folders", () => {
  it("completes paths and keeps recents first", () => {
    expect(isCompletable("~/a")).toBe(true);
    expect(isCompletable("a")).toBe(false);
    const suggested = { targetId: "t", dirs: ["/w/a", "/w/b", "/x"] };
    expect(folderOptions(["/w/a", "/r"], suggested, "/w/", "t")).toEqual(["/w/a", "/r", "/w/b"]);
    expect(folderOptions(["/r"], suggested, "/w/", null)).toEqual(["/r"]);
    expect(folderOptions(["/r"], suggested, "rel", "t")).toEqual(["/r"]);
  });
});

describe("launchDefaults", () => {
  const descriptors = launchCatalogDescriptors();
  const catalog = buildCatalog(descriptors);
  const base = {
    registeredBackends: catalog.ids(),
    descriptors,
    hostDefaultBackend: "codex",
    hostDefaultCwd: "~",
    catalog,
  };

  it("uses the host's agents and defaults", () => {
    const defaults = launchDefaults({ ...base, activeTarget: null });
    expect(defaults.launchableBackends).toEqual(["claude_code", "codex", "opencode"]);
    expect(defaults).toMatchObject({ defaultBackend: "codex", defaultCwd: "~" });
    expect(defaults.defaultLaunchEnvByBackend.codex).toEqual({ CODEX_HOME: "/home/codex" });
    expect(defaults.accountProfilesByBackend.claude_code).toHaveLength(1);
  });

  it("lets the target's agents, defaults, environment and profiles win", () => {
    const defaults = launchDefaults({
      ...base,
      activeTarget: {
        id: "s",
        name: "studio",
        kind: "ssh",
        supported_backends: ["claude_code", "opencode"],
        default_backend: "codex",
        default_cwd: "/srv",
        default_launch_env_by_backend: { claude_code: { X: "1" } },
        account_profiles_by_backend: { claude_code: [] },
      },
    });
    expect(defaults.launchableBackends).toEqual(["claude_code", "opencode"]);
    expect(defaults).toMatchObject({ defaultBackend: "claude_code", defaultCwd: "/srv" });
    expect(defaults.defaultLaunchEnvByBackend.claude_code).toEqual({ X: "1" });
    expect(defaults.accountProfilesByBackend.claude_code).toEqual([]);
  });

  it("discovers threads where the agent supports it, or may", () => {
    expect(threadDiscoveryBackends(["claude_code", "codex", "opencode", "future"], catalog)).toEqual([
      "claude_code",
      "codex",
      "future",
    ]);
  });
});

describe("classifyLaunchFailure", () => {
  it("routes the server's detail tokens and keeps other details", () => {
    expect(classifyLaunchFailure(new AuthError(), "x")).toEqual({ kind: "auth" });
    expect(classifyLaunchFailure(new ApiError(CWD_NOT_FOUND_DETAIL, 400), "x")).toEqual({ kind: "cwd" });
    expect(classifyLaunchFailure(new ApiError(SSH_MASTER_REQUIRED_DETAIL, 409), "x")).toEqual({ kind: "ssh" });
    expect(classifyLaunchFailure(new ApiError("model not found", 400), "x")).toEqual({ kind: "detail", message: "model not found" });
    expect(classifyLaunchFailure("boom", "failed")).toEqual({ kind: "detail", message: "failed" });
  });
});

describe("models", () => {
  it("keeps an unlisted value choosable", () => {
    const options = [{ id: "a", label: "A" }];
    expect(modelChoices(options, "a")).toBe(options);
    expect(modelChoices(options, "z")[0]).toMatchObject({ id: "z", label: "Custom · z" });
    expect(defaultModelLabel("Opus")).toBe("Default (Opus)");
    expect(defaultModelLabel(null)).toBe("Default");
  });
});

describe("withScheduleTiming", () => {
  const request = { backend: "codex", cwd: "/w" };
  const form = { timingMode: "once" as const, recurrence: defaultRecurrenceState(), timing: "delay" as const, delay: "15", at: "" };

  it("fills a delay, a time or a recurrence", () => {
    expect(withScheduleTiming(request, form)).toEqual({ payload: { ...request, delay_seconds: 900 } });
    const at = withScheduleTiming(request, { ...form, timing: "datetime", at: "2030-01-01T09:00" });
    expect("payload" in at && at.payload.scheduled_at).toBe(new Date("2030-01-01T09:00").toISOString());
    const repeat = withScheduleTiming(request, {
      ...form,
      timingMode: "repeat",
      recurrence: { ...defaultRecurrenceState(), cadence: "weekdays", startAt: "2030-01-01T09:30", timezone: "UTC" },
    });
    expect(repeat).toEqual({ payload: { ...request, cron: "30 9 * * 1-5", timezone: "UTC", start_at: "2030-01-01T09:30" } });
  });

  it("explains invalid timing", () => {
    expect(withScheduleTiming(request, { ...form, delay: "-1" })).toEqual({ error: "Enter a non-negative delay in minutes." });
    expect(withScheduleTiming(request, { ...form, timing: "datetime", at: "nope" })).toEqual({ error: "Enter a valid scheduled time." });
    expect(
      withScheduleTiming(request, { ...form, timingMode: "repeat", recurrence: { ...defaultRecurrenceState(), cadence: "custom", customCron: " " } }),
    ).toEqual({ error: "Enter a valid recurrence." });
  });
});

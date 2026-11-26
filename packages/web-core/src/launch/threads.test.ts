import { describe, expect, it } from "vitest";

import { buildCatalog } from "../backends";
import { launchCatalogDescriptors } from "../test-helpers";
import {
  canDeleteThread,
  clampThreadFilter,
  filterThreads,
  formatThreadTime,
  initialThreadFilter,
  mergeThreads,
  showsImportModel,
  threadCounts,
  threadEmptyHint,
  threadFilterOptions,
  threadFormAgent,
  threadDiscoveryBackends,
  threadImportBody,
  threadProfileScope,
  type ThreadSummary,
} from "./threads";

const thread = (id: string, updated: string, extra: Partial<ThreadSummary> = {}): ThreadSummary => ({
  id,
  title: id,
  cwd: "/w",
  created_at: updated,
  updated_at: updated,
  ...extra,
});

const catalog = buildCatalog(launchCatalogDescriptors());
const byBackend = {
  claude_code: [thread("c1", "2026-01-01", { branch: "main" }), thread("c2", "2026-01-03", { title: "Fix bug" })],
  codex: [thread("x1", "2026-01-02", { repo_name: "waypoint" })],
};

describe("threads", () => {
  const all = mergeThreads(byBackend, ["claude_code", "codex"]);

  it("merges agents newest first and counts them", () => {
    expect(all.map((t) => t.id)).toEqual(["c2", "x1", "c1"]);
    expect(threadCounts(byBackend, ["claude_code", "codex"])).toEqual({ all: 3, claude_code: 2, codex: 1 });
  });

  it("offers All only with more than one agent", () => {
    expect(threadFilterOptions(["claude_code", "codex"])).toEqual(["all", "claude_code", "codex"]);
    expect(initialThreadFilter(["codex"])).toBe("codex");
    expect(initialThreadFilter(["codex", "claude_code"])).toBe("all");
    expect(clampThreadFilter("opencode", ["codex"])).toBe("codex");
    expect(clampThreadFilter("codex", ["codex"])).toBe("codex");
  });

  it("filters by agent and the search syntax", () => {
    expect(filterThreads(all, "codex", "").map((t) => t.id)).toEqual(["x1"]);
    expect(filterThreads(all, "all", "branch:main").map((t) => t.id)).toEqual(["c1"]);
    expect(filterThreads(all, "all", "bug OR repo:waypoint").map((t) => t.id)).toEqual(["c2", "x1"]);
  });

  it("imports with the chosen settings only in a single-agent view", () => {
    const choices = { transport: "tmux", launchEnv: { A: "1" }, model: "opus" };
    const defaults = { codex: { CODEX_HOME: "/x" } };
    const scope = { launchTargetId: "", backend: "claude_code", accountProfileId: "work" };
    const base = { choices, scope, importHistory: true, defaultLaunchEnvByBackend: defaults, catalog };
    expect(threadImportBody({ ...base, thread: all[2], filter: "claude_code" })).toEqual({
      thread_id: "c1",
      launch_target_id: null,
      account_profile_id: "work",
      cwd: "/w",
      launch_mode: "auto",
      transport: "tmux",
      launch_env: { A: "1" },
      import_history: true,
      model: "opus",
    });
    expect(threadImportBody({ ...base, thread: all[1], filter: "all" })).toMatchObject({
      account_profile_id: null,
      transport: "codex_app_server",
      launch_env: { CODEX_HOME: "/x" },
      model: null,
    });
  });

  it("scopes import to the form's profile for the form's agent only", () => {
    expect(threadProfileScope("codex", "codex", "work")).toBe("work");
    expect(threadProfileScope("claude_code", "codex", "work")).toBeNull();
    expect(threadProfileScope("codex", "codex", "")).toBeNull();
  });

  it("reads delete and model support from the catalog", () => {
    expect(canDeleteThread("claude_code", catalog)).toBe(true);
    expect(canDeleteThread("codex", catalog)).toBe(false);
    expect(showsImportModel("claude_code", "claude_code", catalog)).toBe(true);
    expect(showsImportModel("all", "claude_code", catalog)).toBe(false);
    expect(threadFormAgent("all", ["codex"], "claude_code")).toBe("codex");
    expect(threadFormAgent("all", ["codex", "claude_code"], "claude_code")).toBe("claude_code");
  });

  it("discovers threads where the agent supports it, or may", () => {
    expect(threadDiscoveryBackends(["claude_code", "codex", "opencode", "future"], catalog)).toEqual([
      "claude_code",
      "codex",
      "future",
    ]);
  });

  it("explains an empty list", () => {
    expect(threadEmptyHint("all", catalog, "studio")).toBe("No importable threads found on studio.");
    expect(threadEmptyHint("codex", catalog, null)).toBe("No Codex threads to resume.");
  });

  it("formats times relative to now", () => {
    const now = Date.parse("2026-01-10T12:00:00Z");
    expect(formatThreadTime("2026-01-10T11:59:30Z", now)).toBe("just now");
    expect(formatThreadTime("2026-01-10T10:00:00Z", now)).toMatch(/2 hours ago/);
  });
});

import { describe, expect, it } from "vitest";

import type { SessionRecord } from "../types";
import { isManaged, sessionDetails, threadIdOf } from "./sessionDetails";

const session = (overrides: Partial<SessionRecord> = {}): SessionRecord =>
  ({
    id: "claude_code-aba234f3",
    backend: "claude_code",
    source: "managed",
    transport: "claude_tty",
    title: "Fix held-message race",
    cwd: "/home/me/waypoint",
    status: "idle",
    created_at: "2026-11-28T09:11:00",
    transport_state: {},
    args: [],
    config_overrides: [],
    ...overrides,
  }) as SessionRecord;

const ids = (rows: ReturnType<typeof sessionDetails>) => rows.map((row) => row.id);
const row = (rows: ReturnType<typeof sessionDetails>, id: string) => rows.find((entry) => entry.id === id);

describe("sessionDetails", () => {
  it("lists the session id first, copyable, and the folder as a copyable path", () => {
    const rows = sessionDetails(session());
    expect(rows[0]).toMatchObject({ id: "session", value: "claude_code-aba234f3", kind: "id", copy: true });
    expect(row(rows, "folder")).toMatchObject({ value: "/home/me/waypoint", kind: "path", copy: true });
  });

  it("orders the facts as the inspector shows them", () => {
    const rows = sessionDetails(
      session({
        transport_state: { thread_id: "thr-1" },
        resolved_model: "claude-opus-4-8",
        model: "opus",
        effort: "high",
        account_profile_label: "Work",
        launch_target_id: "studio",
        worktree_path: "/home/me/wt",
        branch: "fix/held-race",
      }),
    );
    expect(ids(rows)).toEqual([
      "session",
      "thread",
      "folder",
      "agent",
      "interface",
      "model",
      "effort",
      "profile",
      "host",
      "source",
      "created",
      "worktree",
      "branch",
    ]);
  });

  it("leaves out a fact the session lacks", () => {
    const rows = sessionDetails(session());
    expect(ids(rows)).not.toContain("thread");
    expect(ids(rows)).not.toContain("effort");
    expect(ids(rows)).not.toContain("profile");
    expect(ids(rows)).not.toContain("worktree");
    expect(ids(rows)).not.toContain("branch");
  });

  it("reads the thread id from the transport state only when it is text", () => {
    expect(threadIdOf(session({ transport_state: { thread_id: "t-9" } }))).toBe("t-9");
    expect(threadIdOf(session({ transport_state: { thread_id: 7 } }))).toBeNull();
    expect(threadIdOf(session({ transport_state: { thread_id: "" } }))).toBeNull();
    expect(threadIdOf(session())).toBeNull();
  });

  it("labels a confirmed model by the one that ran, and an unconfirmed one as such", () => {
    const confirmed = row(sessionDetails(session({ resolved_model: "claude-opus-4-8", model: "opus" })), "model");
    expect(confirmed?.note).toBeUndefined();
    expect(confirmed?.title).toBe("claude-opus-4-8");
    expect(confirmed?.value).toMatch(/Opus/);

    const selected = row(sessionDetails(session({ model: "opus", resolved_model: null })), "model");
    expect(selected).toMatchObject({ value: "Opus", note: "unconfirmed" });

    expect(row(sessionDetails(session({ model: null, resolved_model: null })), "model")).toBeUndefined();
  });

  it("names the host by its target, else the target id, else Local", () => {
    expect(row(sessionDetails(session({ launch_target_id: "s0" }), { launchTargetName: "Sandbox 0" }), "host")?.value).toBe("Sandbox 0");
    expect(row(sessionDetails(session({ launch_target_id: "s0" })), "host")?.value).toBe("s0");
    expect(row(sessionDetails(session({ launch_target_id: null })), "host")?.value).toBe("Local");
  });

  it("prefers the live branch to the one the session launched on", () => {
    expect(row(sessionDetails(session({ branch: "main" }), { liveBranch: "fix/x" }), "branch")?.value).toBe("fix/x");
    expect(row(sessionDetails(session({ branch: "main" })), "branch")?.value).toBe("main");
  });

  it("calls attached and managed sessions by how they are held", () => {
    expect(row(sessionDetails(session({ source: "managed" })), "source")?.value).toBe("Managed");
    expect(row(sessionDetails(session({ source: "attached_tmux" })), "source")?.value).toBe("Attached");
    expect(isManaged(session({ source: "managed" }))).toBe(true);
    expect(isManaged(session({ source: "attached_tmux" }))).toBe(false);
  });

  it("gives the assistant no folder or interface", () => {
    const rows = sessionDetails(session({ source: "assistant" }));
    expect(ids(rows)).not.toContain("folder");
    expect(ids(rows)).not.toContain("interface");
    expect(row(rows, "source")?.value).toBe("Assistant");
  });

  it("shows plain words in sans and machine strings as ids", () => {
    const rows = sessionDetails(session({ effort: "high", branch: "main", transport_state: { thread_id: "t" } }));
    for (const id of ["agent", "interface", "effort", "host", "source", "created"]) expect(row(rows, id)?.kind).toBe("text");
    for (const id of ["session", "thread", "branch"]) expect(row(rows, id)?.kind).toBe("id");
  });

  it("formats the creation time without a year in the current year", () => {
    const created = row(sessionDetails(session({ created_at: "2020-01-02T03:04:00" })), "created")?.value;
    expect(created).toBe("Jan 2, 2020, 03:04");
  });
});

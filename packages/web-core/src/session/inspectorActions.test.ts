import { describe, expect, it } from "vitest";

import { inspectorActions, sessionEndAction, type InspectorActionOptions } from "./inspectorActions";

const options = (overrides: Partial<InspectorActionOptions> = {}): InspectorActionOptions => ({
  surface: "sheet",
  session: { status: "idle", pinned_at: null },
  hasPane: true,
  terminalOnly: false,
  view: "chat",
  composing: true,
  dormant: false,
  hasFiles: true,
  ...overrides,
});

const ids = (overrides?: Partial<InspectorActionOptions>) => inspectorActions(options(overrides)).main.map((action) => action.id);

describe("sessionEndAction", () => {
  it("terminates a live session and deletes a stopped one", () => {
    expect(sessionEndAction({ status: "running" })).toBe("terminate");
    expect(sessionEndAction({ status: "error" })).toBe("terminate");
    expect(sessionEndAction({ status: "exited" })).toBe("delete");
  });
});

describe("inspectorActions on the sheet", () => {
  it("lists every tile for a live chat session with a terminal pane", () => {
    expect(ids()).toEqual(["switch", "terminal", "files", "schedule", "settings", "rename", "pin", "refresh"]);
  });

  it("offers Chat in the terminal view and no switch for a terminal-only or pane-less transport", () => {
    expect(ids({ view: "terminal" })).toContain("chat");
    expect(ids({ view: "terminal" })).not.toContain("terminal");
    expect(ids({ terminalOnly: true })).not.toContain("terminal");
    expect(ids({ hasPane: false })).not.toContain("terminal");
  });

  it("turns Pin into Unpin for a pinned session", () => {
    expect(ids({ session: { status: "idle", pinned_at: "2026-11-28T00:00:00Z" } })).toContain("unpin");
    expect(ids({ session: { status: "idle", pinned_at: "2026-11-28T00:00:00Z" } })).not.toContain("pin");
  });

  it("offers Schedule only while a composer can host it, and files only where they exist", () => {
    expect(ids({ composing: false })).not.toContain("schedule");
    expect(ids({ hasFiles: false })).not.toContain("files");
  });

  it("offers Reconnect only for a stopped session that can be brought back", () => {
    expect(inspectorActions(options()).reconnect).toBeNull();
    expect(inspectorActions(options({ dormant: true })).reconnect).toMatchObject({ id: "reconnect" });
  });

  it("sets Terminate apart, and Delete transcript for a stopped session", () => {
    expect(inspectorActions(options()).end).toEqual({ id: "terminate", label: "Terminate session", danger: true });
    expect(inspectorActions(options({ session: { status: "exited", pinned_at: null } })).end).toEqual({
      id: "delete",
      label: "Delete transcript",
      danger: true,
    });
  });
});

describe("inspectorActions in the side pane", () => {
  const pane = (overrides?: Partial<InspectorActionOptions>) => inspectorActions(options({ surface: "pane", ...overrides }));

  it("keeps only what its header menu lacks", () => {
    expect(pane().main.map((action) => action.id)).toEqual(["schedule", "settings"]);
    expect(pane({ composing: false }).main.map((action) => action.id)).toEqual(["settings"]);
  });

  it("labels the end action for the pane", () => {
    expect(pane().end.label).toBe("Terminate");
  });
});

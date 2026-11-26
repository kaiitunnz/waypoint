import { describe, expect, it } from "vitest";

import {
  addEnvEntry,
  editEnvValue,
  entriesToRecord,
  envRowState,
  envSummary,
  envValueDisplay,
  recordToEntries,
  stageEnvRemoval,
  undoEnvEntry,
  updateEnvEntry,
} from "./env";

describe("env rows", () => {
  const rows = recordToEntries({ GH_TOKEN: "ghp_abc", UV_CACHE_DIR: "/mnt/uv" });

  it("round-trips a record, dropping blank keys and keeping the last duplicate", () => {
    expect(entriesToRecord(rows)).toEqual({ GH_TOKEN: "ghp_abc", UV_CACHE_DIR: "/mnt/uv" });
    const dup = [...rows, { id: 9, key: " GH_TOKEN ", value: "new" }, { id: 10, key: " ", value: "x" }];
    expect(entriesToRecord(dup)).toEqual({ GH_TOKEN: "new", UV_CACHE_DIR: "/mnt/uv" });
  });

  it("stages an edit and undoes it to the original value", () => {
    const edited = editEnvValue(editEnvValue(rows, 2, "/a"), 2, "/b");
    expect(envRowState(edited[1])).toBe("edit");
    expect(edited[1]).toMatchObject({ value: "/b", original: "/mnt/uv" });
    expect(entriesToRecord(edited).UV_CACHE_DIR).toBe("/b");
    const undone = undoEnvEntry(edited, 2);
    expect(undone[1]).toEqual({ id: 2, key: "UV_CACHE_DIR", value: "/mnt/uv" });
    expect(envRowState(undone[1])).toBe("keep");
  });

  it("strikes a removed row until undone, restoring an edited value first", () => {
    const removed = stageEnvRemoval(editEnvValue(rows, 1, "changed"), 1);
    expect(envRowState(removed[0])).toBe("remove");
    expect(removed[0].value).toBe("ghp_abc");
    expect(entriesToRecord(removed)).toEqual({ UV_CACHE_DIR: "/mnt/uv" });
    expect(envSummary(removed)).toBe("UV_CACHE_DIR");
    expect(undoEnvEntry(removed, 1)[0]).toEqual({ id: 1, key: "GH_TOKEN", value: "ghp_abc" });
  });

  it("drops an added row on remove or undo", () => {
    const added = updateEnvEntry(addEnvEntry(rows), 3, { key: "DEBUG", value: "1" });
    expect(envRowState(added[2])).toBe("add");
    expect(entriesToRecord(added).DEBUG).toBe("1");
    expect(stageEnvRemoval(added, 3)).toHaveLength(2);
    expect(undoEnvEntry(added, 3)).toHaveLength(2);
    expect(editEnvValue(added, 3, "2")[2].original).toBeUndefined();
  });

  it("masks secret-looking values and shortens long ones", () => {
    expect(envValueDisplay("GH_TOKEN", "ghp_abcdef")).toBe("ghp_••••••••");
    expect(envValueDisplay("API_KEY", "abcdef")).toBe("••••••••");
    expect(envValueDisplay("UV_CACHE_DIR", "/mnt/uv")).toBe("/mnt/uv");
    expect(envValueDisplay("PATHS", "x".repeat(40))).toHaveLength(28);
    expect(envValueDisplay("EMPTY", "")).toBe("");
  });
});

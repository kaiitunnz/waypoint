import { describe, expect, it } from "vitest";

import type { ExistingEnvEntry, NewEnvEntry } from "../hooks/useSessionSettings";
import { addEnvEntry, editEnvValue, stageEnvRemoval, undoEnvEntry, updateEnvEntry } from "../launch/env";
import { applyEnvEntries, settingsEnvEntries } from "./settingsEnv";

const keep = (key: string): ExistingEnvEntry => ({ key, op: "keep", value: "" });
const EXISTING = [keep("GH_TOKEN"), keep("UV_CACHE_DIR"), keep("DEBUG")];

describe("settingsEnvEntries", () => {
  it("shows each existing key as an unedited row with no value", () => {
    expect(settingsEnvEntries(EXISTING, [])).toEqual([
      { id: 1, key: "GH_TOKEN", value: "" },
      { id: 2, key: "UV_CACHE_DIR", value: "" },
      { id: 3, key: "DEBUG", value: "" },
    ]);
  });

  it("shows a replace as an edit and a remove as a removal", () => {
    const rows = settingsEnvEntries([{ key: "A", op: "replace", value: "x" }, { key: "B", op: "remove", value: "" }], []);
    expect(rows).toEqual([
      { id: 1, key: "A", value: "x", original: "" },
      { id: 2, key: "B", value: "", removed: true },
    ]);
  });

  it("leaves out a hidden key without renumbering the rest", () => {
    const rows = settingsEnvEntries(EXISTING, [], ["GH_TOKEN"]);
    expect(rows.map((row) => [row.id, row.key])).toEqual([
      [2, "UV_CACHE_DIR"],
      [3, "DEBUG"],
    ]);
  });

  it("lists added rows after the existing ones with their own ids", () => {
    const added: NewEnvEntry[] = [{ id: 4, key: "NEW", value: "v" }];
    const rows = settingsEnvEntries(EXISTING, added);
    expect(rows.at(-1)).toEqual({ id: 4, key: "NEW", value: "v", added: true });
  });
});

describe("applyEnvEntries", () => {
  const round = (existing: ExistingEnvEntry[], added: NewEnvEntry[], hidden: string[] = []) =>
    applyEnvEntries(settingsEnvEntries(existing, added, hidden), existing);

  it("round-trips untouched, replaced, removed and added rows", () => {
    const existing: ExistingEnvEntry[] = [keep("A"), { key: "B", op: "replace", value: "new" }, { key: "C", op: "remove", value: "" }];
    const added: NewEnvEntry[] = [{ id: 4, key: "D", value: "d" }];
    expect(round(existing, added)).toEqual({ existing, added });
  });

  it("keeps a hidden key as it was, whatever the rows say", () => {
    const existing: ExistingEnvEntry[] = [{ key: "HIDDEN", op: "remove", value: "" }, keep("B")];
    expect(round(existing, [], ["HIDDEN"]).existing).toEqual(existing);
  });

  it("applies the launch editor's operations to the existing keys", () => {
    let rows = settingsEnvEntries(EXISTING, []);
    rows = editEnvValue(rows, 2, "/mnt/cache");
    rows = stageEnvRemoval(rows, 3);
    expect(applyEnvEntries(rows, EXISTING).existing).toEqual([keep("GH_TOKEN"), { key: "UV_CACHE_DIR", op: "replace", value: "/mnt/cache" }, { key: "DEBUG", op: "remove", value: "" }]);
    rows = undoEnvEntry(undoEnvEntry(rows, 2), 3);
    expect(applyEnvEntries(rows, EXISTING).existing).toEqual(EXISTING);
  });

  it("starts a replace from an empty value, so an unfilled replace is a no-op for the hook", () => {
    const rows = editEnvValue(settingsEnvEntries(EXISTING, []), 1, "");
    expect(applyEnvEntries(rows, EXISTING).existing[0]).toEqual({ key: "GH_TOKEN", op: "replace", value: "" });
  });

  it("keeps an added row's id as the editor numbers it, even where it equals a hidden key's", () => {
    // The last key is hidden, so the first added row takes id 2 — the hidden key's own id.
    const existing = [keep("A"), keep("HIDDEN")];
    let rows = settingsEnvEntries(existing, [], ["HIDDEN"]);
    rows = addEnvEntry(rows);
    rows = updateEnvEntry(rows, 2, { key: "NEW", value: "v" });
    const applied = applyEnvEntries(rows, existing);
    expect(applied.added).toEqual([{ id: 2, key: "NEW", value: "v" }]);
    expect(applied.existing).toEqual(existing);
    // And back: the added row survives with the same id.
    expect(settingsEnvEntries(applied.existing, applied.added, ["HIDDEN"]).map((row) => row.id)).toEqual([1, 2]);
  });
});

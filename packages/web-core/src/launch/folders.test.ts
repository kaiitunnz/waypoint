import { describe, expect, it } from "vitest";

import { folderOptions, isCompletable } from "./folders";

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

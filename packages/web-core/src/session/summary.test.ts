import { describe, expect, it } from "vitest";

import { plainSummary, plainTitle, summaryParts } from "./summary";

describe("summaryParts", () => {
  it("marks what stands between backticks as machine text", () => {
    expect(summaryParts("Read `a.py`, search `x y`")).toEqual([
      { text: "Read ", code: false },
      { text: "a.py", code: true },
      { text: ", search ", code: false },
      { text: "x y", code: true },
    ]);
    expect(summaryParts("Run the tests")).toEqual([{ text: "Run the tests", code: false }]);
  });

  it("leaves a line with an unpaired backtick as written", () => {
    expect(summaryParts("Quote a ` mark")).toEqual([{ text: "Quote a ` mark", code: false }]);
    expect(plainSummary("Quote a ` mark")).toBe("Quote a ` mark");
  });

  it("reads plain without its marks", () => {
    expect(plainSummary("Read `SKILL.md`")).toBe("Read SKILL.md");
  });
});

describe("plainTitle", () => {
  it("drops the marks of a summary, never those of a request's own title", () => {
    expect(plainTitle({ text: "Read `a`", marked: true })).toBe("Read a");
    expect(plainTitle({ text: "echo `date`", marked: false })).toBe("echo `date`");
  });
});

import { describe, expect, it } from "vitest";

import { foldLines } from "./foldText";

describe("foldLines", () => {
  const text = "a\nb\nc\nd\ne";

  it("keeps all of a text that fits", () => {
    expect(foldLines(text, 5)).toEqual({ text, total: 5, hidden: 0 });
    expect(foldLines("one", 3)).toEqual({ text: "one", total: 1, hidden: 0 });
  });

  it("keeps the start of a longer text", () => {
    expect(foldLines(text, 2)).toEqual({ text: "a\nb", total: 5, hidden: 3 });
  });

  it("ignores blank lines at the ends and reads CRLF", () => {
    expect(foldLines("\n\nx\r\ny\r\n\n", 5)).toEqual({ text: "x\ny", total: 2, hidden: 0 });
  });

  it("has no lines for blank text", () => {
    expect(foldLines(" \n ", 3)).toEqual({ text: "", total: 0, hidden: 0 });
  });
});

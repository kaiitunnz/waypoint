import { describe, expect, it } from "vitest";

import { formatCwdSegments, matchControlCommand, stripAnsi, wrapBracketedPaste } from "./terminalText";

describe("matchControlCommand", () => {
  it("returns an empty argument string for the bare command", () => {
    expect(matchControlCommand("/new", "new")).toBe("");
  });

  it("returns the trimmed trailing argument", () => {
    expect(matchControlCommand("/new  fix the bug  ", "new")).toBe("fix the bug");
    expect(matchControlCommand("/new ", "new")).toBe("");
  });

  it("requires the separating space, so a longer command name is not matched", () => {
    expect(matchControlCommand("/news", "new")).toBeNull();
    expect(matchControlCommand("/new-thing x", "new")).toBeNull();
  });

  it("rejects other commands and plain text", () => {
    expect(matchControlCommand("/fork", "new")).toBeNull();
    expect(matchControlCommand("new", "new")).toBeNull();
    expect(matchControlCommand(" /new", "new")).toBeNull();
    expect(matchControlCommand("", "new")).toBeNull();
  });
});

describe("formatCwdSegments", () => {
  it("returns a single empty segment for an empty path", () => {
    expect(formatCwdSegments("")).toEqual([""]);
  });

  it("keeps the leading slash on the first segment of an absolute path", () => {
    expect(formatCwdSegments("/home/me/project")).toEqual(["/home", "me", "project"]);
    expect(formatCwdSegments("/home")).toEqual(["/home"]);
  });

  it("returns an empty segment for the root, which has no name once slashes are trimmed", () => {
    expect(formatCwdSegments("/")).toEqual([""]);
    expect(formatCwdSegments("///")).toEqual([""]);
  });

  it("ignores trailing and repeated slashes", () => {
    expect(formatCwdSegments("/a//b/")).toEqual(["/a", "b"]);
  });

  it("splits relative and home-relative paths without a leading slash", () => {
    expect(formatCwdSegments("~/work/repo")).toEqual(["~", "work", "repo"]);
    expect(formatCwdSegments("a/b")).toEqual(["a", "b"]);
  });
});

describe("wrapBracketedPaste", () => {
  it("wraps the payload in bracketed-paste markers", () => {
    expect(wrapBracketedPaste("hi")).toBe("\u001b[200~hi\u001b[201~");
  });

  it("normalises CRLF and lone CR to LF", () => {
    expect(wrapBracketedPaste("a\r\nb\rc\nd")).toBe("\u001b[200~a\nb\nc\nd\u001b[201~");
  });

  it("wraps an empty payload", () => {
    expect(wrapBracketedPaste("")).toBe("\u001b[200~\u001b[201~");
  });
});

describe("stripAnsi", () => {
  it("removes CSI colour and cursor sequences", () => {
    expect(stripAnsi("\u001b[31mred\u001b[0m \u001b[2K\u001b[1;1Hok")).toBe("red ok");
  });

  it("removes OSC sequences terminated by BEL or ST", () => {
    expect(stripAnsi("a\u001b]0;title\u0007b")).toBe("ab");
    expect(stripAnsi("a\u001b]8;;http://x\u001b\\link\u001b]8;;\u001b\\b")).toBe("alinkb");
  });

  it("removes two-character escapes in the @ to _ range and keeps the rest", () => {
    expect(stripAnsi("a\u001bMb")).toBe("ab");
    expect(stripAnsi("a\u001b=b")).toBe("a\u001b=b");
  });

  it("leaves plain text and newlines untouched", () => {
    expect(stripAnsi("line1\nline2 é")).toBe("line1\nline2 é");
  });
});

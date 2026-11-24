import { describe, expect, it } from "vitest";

import { stripAnsi, wrapBracketedPaste } from "./terminalText";

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

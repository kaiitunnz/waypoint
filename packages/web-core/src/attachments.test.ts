import { describe, expect, it } from "vitest";

import { attachmentSpecsFor, formatFileSize } from "./attachments";
import { ev } from "./test-helpers";

describe("attachmentSpecsFor", () => {
  it("reads the specs off the event metadata", () => {
    const specs = [{ id: "a", filename: "a.txt", kind: "file", size: 1 }];
    expect(attachmentSpecsFor(ev(1, "user_input", "", { attachments: specs }))).toEqual(specs);
    expect(attachmentSpecsFor(ev(2, "user_input", "", { attachments: "nope" }))).toEqual([]);
    expect(attachmentSpecsFor(ev(3, "user_input"))).toEqual([]);
  });
});

describe("formatFileSize", () => {
  it("scales through KB to TB with one decimal under ten", () => {
    expect(formatFileSize(812)).toBe("812 B");
    expect(formatFileSize(2048)).toBe("2.0 KB");
    expect(formatFileSize(1024 * 1024 * 2.4)).toBe("2.4 MB");
    expect(formatFileSize(1024 * 1024 * 50)).toBe("50 MB");
    expect(formatFileSize(1024 ** 5 * 3)).toBe("3072 TB");
  });
});

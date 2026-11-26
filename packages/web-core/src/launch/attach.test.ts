import { describe, expect, it } from "vitest";

import { attachRequest, attachTargetError } from "./attach";

describe("attach", () => {
  it("needs a pane and sends a title only when given", () => {
    expect(attachTargetError("  ")).toBe("Enter a tmux pane.");
    expect(attachTargetError("main:0.0")).toBeNull();
    expect(attachRequest(" main:0.0 ", "codex", " ")).toEqual({ tmux_target: "main:0.0", backend_hint: "codex" });
    expect(attachRequest("a", "codex", " T ")).toMatchObject({ title: "T" });
  });
});

import { describe, expect, it } from "vitest";

import { ev } from "../test-helpers";
import { isBlankAgentMessage, modelChangeText, reasoningPreview, sendUserFileInput } from "./cardText";

describe("reasoningPreview", () => {
  it("flattens bold and whitespace", () => {
    expect(reasoningPreview("**Plan**\n\nread  the file")).toBe("Plan read the file");
  });

  it("truncates a long line with an ellipsis", () => {
    const preview = reasoningPreview("x".repeat(300), 20);
    expect(preview).toHaveLength(20);
    expect(preview.endsWith("…")).toBe(true);
  });
});

describe("isBlankAgentMessage", () => {
  it("is true only for a whitespace-only agent message", () => {
    expect(isBlankAgentMessage(ev(1, "agent_output", " \n"))).toBe(true);
    expect(isBlankAgentMessage(ev(2, "agent_output", "hi"))).toBe(false);
    expect(isBlankAgentMessage(ev(3, "user_input", ""))).toBe(false);
  });
});

describe("sendUserFileInput", () => {
  it("reads the files and caption off the call", () => {
    const event = ev(1, "tool_call", "", { payload: { input: { files: ["/a", "", 3, "/b"], caption: " see " } } });
    expect(sendUserFileInput(event)).toEqual({ files: ["/a", "/b"], caption: " see " });
    expect(sendUserFileInput(ev(2, "tool_call"))).toEqual({ files: [], caption: null });
  });
});

describe("modelChangeText", () => {
  it("describes a change and a switch", () => {
    const change = ev(1, "system_note", "", { previous_model: "a", current_model: "b" });
    expect(modelChangeText(change)).toBe("Model changed from a to b");
    expect(modelChangeText(ev(2, "system_note", "", { current_model: "b" }))).toBe("Switched to b");
  });
});

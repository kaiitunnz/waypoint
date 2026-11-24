import { describe, expect, it } from "vitest";

import { effortLabel, formatResolvedModelLabel, modelLabelFor } from "./modelDisplay";
import type { BackendModelOption } from "./types";

const options = [
  { id: "claude-opus-4-8", label: "Opus 4.8 (catalog)" },
  { id: "gpt-5", label: "GPT-5" },
] as BackendModelOption[];

describe("effortLabel", () => {
  it("maps known levels and passes unknown ones through", () => {
    expect(effortLabel("xhigh")).toBe("Extra high");
    expect(effortLabel("none")).toBe("None");
    expect(effortLabel("max")).toBe("Max");
    expect(effortLabel("turbo")).toBe("turbo");
  });
});

describe("formatResolvedModelLabel", () => {
  it("is null without a resolved model", () => {
    expect(formatResolvedModelLabel(null, "opus", options)).toBeNull();
    expect(formatResolvedModelLabel(undefined, null, options)).toBeNull();
    expect(formatResolvedModelLabel("", null, options)).toBeNull();
  });

  it("prefers the catalog label", () => {
    expect(formatResolvedModelLabel("claude-opus-4-8", null, options)).toBe("Opus 4.8 (catalog)");
    expect(formatResolvedModelLabel("gpt-5", null, options)).toBe("GPT-5");
  });

  it.each([
    ["claude-opus-4-8", "Opus 4.8"],
    ["claude-sonnet-5", "Sonnet 5"],
    ["claude-haiku-5-5", "Haiku 5.5"],
    ["claude-fable-5-2", "Fable 5.2"],
    ["claude-opus-4-8-20260101", "Opus 4.8.20260101"],
  ])("parses the concrete claude id %s as %s", (id, label) => {
    expect(formatResolvedModelLabel(id, null, [])).toBe(label);
  });

  it("falls back to the raw id for ids it cannot parse", () => {
    expect(formatResolvedModelLabel("claude-opus", null, [])).toBe("claude-opus");
    expect(formatResolvedModelLabel("claude-unknown-1", null, [])).toBe("claude-unknown-1");
    expect(formatResolvedModelLabel("o3-mini", null, [])).toBe("o3-mini");
  });

  it("reattaches the 1M context variant from the selection", () => {
    expect(formatResolvedModelLabel("claude-opus-4-8", "opus[1m]", [])).toBe("Opus 4.8 (1M context)");
    expect(formatResolvedModelLabel("claude-opus-4-8", "opus", [])).toBe("Opus 4.8");
    expect(formatResolvedModelLabel("gpt-5", "x[1m]", options)).toBe("GPT-5 (1M context)");
  });
});

describe("modelLabelFor", () => {
  it("labels a stored id, falling back to the id itself", () => {
    expect(modelLabelFor("gpt-5", options)).toBe("GPT-5");
    expect(modelLabelFor("claude-sonnet-5", options)).toBe("Sonnet 5");
    expect(modelLabelFor("mystery", options)).toBe("mystery");
    expect(modelLabelFor("", options)).toBe("");
  });
});

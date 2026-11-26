import { describe, expect, it } from "vitest";

import { defaultModelChoiceLabel, effortChoices, effortLevelsFor, modelChoices } from "./models";

describe("models", () => {
  it("keeps an unlisted value choosable", () => {
    const options = [{ id: "a", label: "A" }];
    expect(modelChoices(options, "a")).toEqual(options);
    expect(modelChoices(options, "z")[0]).toMatchObject({ id: "z", label: "Custom · z" });
    expect(effortChoices(["low", "high"], "max")).toEqual(["max", "low", "high"]);
    expect(effortChoices(["low", "high"], "low")).toEqual(["low", "high"]);
    expect(defaultModelChoiceLabel("Opus")).toBe("Default (Opus)");
    expect(defaultModelChoiceLabel(null)).toBe("Default");
  });

  it("offers the levels of the chosen or default model", () => {
    const models = [
      { id: "a", label: "A", supported_efforts: ["low"] },
      { id: "b", label: "B", supported_efforts: null },
      { id: "c", label: "C", supported_efforts: ["high"] },
    ];
    expect(effortLevelsFor(models, ["low", "max"], "a", null)).toEqual(["low"]);
    expect(effortLevelsFor(models, ["low", "max"], "b", null)).toEqual(["low", "max"]);
    expect(effortLevelsFor(models, ["low", "max"], null, "c")).toEqual(["high"]);
    expect(effortLevelsFor(models, ["low", "max"], null, null)).toEqual(["low", "high"]);
  });
});

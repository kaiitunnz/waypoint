import { createElement } from "react";
import { describe, expect, it } from "vitest";

import { fenceLanguage, nodeToText } from "./markdownCode";

const code = (text: string, className?: string) => createElement("code", { className }, text);

describe("nodeToText", () => {
  it("reads strings and numbers", () => {
    expect(nodeToText("a")).toBe("a");
    expect(nodeToText(7)).toBe("7");
  });

  it("walks nested elements and arrays in order", () => {
    const tree = createElement("pre", null, createElement("code", null, ["let ", createElement("span", null, "x"), " = ", 1]));
    expect(nodeToText(tree)).toBe("let x = 1");
  });

  it("ignores values that render nothing", () => {
    expect(nodeToText(null)).toBe("");
    expect(nodeToText(false)).toBe("");
    expect(nodeToText(undefined)).toBe("");
  });
});

describe("fenceLanguage", () => {
  it("reads the language react-markdown stamps on the code element", () => {
    expect(fenceLanguage(code("x", "language-ts"))).toBe("ts");
    expect(fenceLanguage([code("x", "hljs language-python extra")])).toBe("python");
  });

  it("is empty for a fence with no language", () => {
    expect(fenceLanguage(code("x"))).toBe("");
    expect(fenceLanguage(code("x", "other-class"))).toBe("");
    expect(fenceLanguage("plain text")).toBe("");
  });
});

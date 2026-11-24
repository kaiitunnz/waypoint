import { describe, expect, it } from "vitest";

import { variantLabel } from "./variant";

describe("variantLabel", () => {
  it("names each frontend variant", () => {
    expect(variantLabel("v1")).toBe("Waypoint v1");
    expect(variantLabel("v2")).toBe("Waypoint v2");
  });
});

import { afterEach, describe, expect, it, vi } from "vitest";

import { DEFAULT_BACKEND_PORT, deploymentBackendPort } from "./tailnet";

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("deploymentBackendPort", () => {
  it("is the default when the variable is unset or empty", () => {
    vi.stubEnv("NEXT_PUBLIC_BACKEND_PORT", undefined);
    expect(deploymentBackendPort()).toBe(DEFAULT_BACKEND_PORT);
    vi.stubEnv("NEXT_PUBLIC_BACKEND_PORT", "");
    expect(deploymentBackendPort()).toBe(DEFAULT_BACKEND_PORT);
  });

  it("reads a valid port", () => {
    vi.stubEnv("NEXT_PUBLIC_BACKEND_PORT", "8797");
    expect(deploymentBackendPort()).toBe(8797);
  });

  it("falls back to the default for a value that is not a port", () => {
    for (const value of ["0", "70000", "-1", "abc"]) {
      vi.stubEnv("NEXT_PUBLIC_BACKEND_PORT", value);
      expect(deploymentBackendPort()).toBe(DEFAULT_BACKEND_PORT);
    }
  });
});

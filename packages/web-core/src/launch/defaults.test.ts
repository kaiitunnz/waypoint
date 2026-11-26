import { describe, expect, it } from "vitest";

import { buildCatalog } from "../backends";
import { launchCatalogDescriptors } from "../test-helpers";
import { launchDefaults } from "./defaults";

describe("launchDefaults", () => {
  const descriptors = launchCatalogDescriptors();
  const catalog = buildCatalog(descriptors);
  const base = {
    registeredBackends: catalog.ids(),
    descriptors,
    hostDefaultBackend: "codex",
    hostDefaultCwd: "~",
    catalog,
  };

  it("uses the host's agents and defaults", () => {
    const defaults = launchDefaults({ ...base, activeTarget: null });
    expect(defaults.launchableBackends).toEqual(["claude_code", "codex", "opencode"]);
    expect(defaults).toMatchObject({ defaultBackend: "codex", defaultCwd: "~" });
    expect(defaults.defaultLaunchEnvByBackend.codex).toEqual({ CODEX_HOME: "/home/codex" });
    expect(defaults.accountProfilesByBackend.claude_code).toHaveLength(1);
  });

  it("lets the target's agents, defaults, environment and profiles win", () => {
    const defaults = launchDefaults({
      ...base,
      activeTarget: {
        id: "s",
        name: "studio",
        kind: "ssh",
        supported_backends: ["claude_code", "opencode"],
        default_backend: "codex",
        default_cwd: "/srv",
        default_launch_env_by_backend: { claude_code: { X: "1" } },
        account_profiles_by_backend: { claude_code: [] },
      },
    });
    expect(defaults.launchableBackends).toEqual(["claude_code", "opencode"]);
    expect(defaults).toMatchObject({ defaultBackend: "claude_code", defaultCwd: "/srv" });
    expect(defaults.defaultLaunchEnvByBackend.claude_code).toEqual({ X: "1" });
    expect(defaults.accountProfilesByBackend.claude_code).toEqual([]);
  });
});

import { describe, expect, it } from "vitest";

import { buildCatalog, readsDialogsFromScreen } from "./backends";
import type { BackendDescriptor } from "./types";

const descriptor = (id: string, transport_id: string, has_terminal_pane: boolean) =>
  ({ id, transport_id, label: id, transport_capabilities: { has_terminal_pane } }) as unknown as BackendDescriptor;

describe("readsDialogsFromScreen", () => {
  const catalog = buildCatalog([descriptor("a", "pane_tail", true), descriptor("b", "protocol", false)]);

  it("follows the transport's terminal pane", () => {
    expect(readsDialogsFromScreen("pane_tail", catalog)).toBe(true);
    expect(readsDialogsFromScreen("protocol", catalog)).toBe(false);
  });

  it("knows the built-in transports before the catalog is read", () => {
    expect(readsDialogsFromScreen("claude_tty")).toBe(true);
    expect(readsDialogsFromScreen("claude_cli")).toBe(false);
    expect(readsDialogsFromScreen("codex_app_server")).toBe(false);
    expect(readsDialogsFromScreen("opencode_http")).toBe(false);
  });

  it("takes it as so for a transport it does not know", () => {
    expect(readsDialogsFromScreen(undefined, catalog)).toBe(true);
    expect(readsDialogsFromScreen("later_transport", catalog)).toBe(true);
  });
});

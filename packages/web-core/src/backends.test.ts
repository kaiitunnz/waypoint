import { describe, expect, it } from "vitest";

import { buildCatalog, readsDialogsFromScreen } from "./backends";
import type { BackendDescriptor } from "./types";

const descriptor = (id: string, transport_id: string, has_terminal_pane: boolean, is_structured = true) =>
  ({ id, transport_id, label: id, transport_capabilities: { has_terminal_pane, is_structured } }) as unknown as BackendDescriptor;

describe("readsDialogsFromScreen", () => {
  const catalog = buildCatalog([
    descriptor("a", "pane_tail", true),
    descriptor("b", "protocol", false),
    descriptor("c", "scraped", false, false),
  ]);

  it("takes it from a terminal pane or a missing structured stream", () => {
    expect(readsDialogsFromScreen("pane_tail", catalog)).toBe(true);
    expect(readsDialogsFromScreen("protocol", catalog)).toBe(false);
    expect(readsDialogsFromScreen("scraped", catalog)).toBe(true);
  });

  it("knows the built-in transports before the catalog is read", () => {
    expect(readsDialogsFromScreen("claude_tty")).toBe(true);
    expect(readsDialogsFromScreen("tmux")).toBe(true);
    expect(readsDialogsFromScreen("claude_cli")).toBe(false);
    expect(readsDialogsFromScreen("codex_app_server")).toBe(false);
    expect(readsDialogsFromScreen("opencode_http")).toBe(false);
  });

  it("takes it as so for a transport it does not know", () => {
    expect(readsDialogsFromScreen(undefined, catalog)).toBe(true);
    expect(readsDialogsFromScreen("later_transport", catalog)).toBe(true);
  });
});

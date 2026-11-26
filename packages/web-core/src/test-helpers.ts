import type { BackendDescriptor, EventKind, EventRecord } from "./types";

export function ev(
  sequence: number,
  kind: EventKind,
  text = "",
  metadata: Record<string, unknown> = {},
  extra: Partial<EventRecord> = {},
): EventRecord {
  return {
    id: sequence,
    session_id: "s1",
    ts: `2026-01-01T00:00:${String(sequence).padStart(2, "0")}Z`,
    kind,
    text,
    metadata,
    sequence,
    ...extra,
  };
}

export function planMetadata(itemId: string, text = "the plan"): Record<string, unknown> {
  return { item_type: "plan", item_id: itemId, payload: { item: { text } } };
}

// A catalog shaped like the built-in agents: Claude Code (three interfaces,
// profiles, permission modes), Codex (no effort flag over tmux), OpenCode (no
// model or effort flag over tmux), and the tmux fallback.
export function launchCatalogDescriptors(): BackendDescriptor[] {
  const transportCaps = (structured: boolean, fallback = false) => ({
    is_structured: structured,
    is_fallback_for_managed_launch: fallback,
  });
  const agent = (
    id: string,
    label: string,
    transport: string,
    supported: string[],
    agentCaps: Record<string, unknown>,
    flat: Record<string, unknown> = {},
  ) =>
    ({
      id,
      label,
      transport_id: transport,
      supported_transports: supported,
      default_transport: supported[0],
      badges: {},
      default_launch_env: { [`${id.toUpperCase()}_HOME`]: `/home/${id}` },
      account_profiles: id === "claude_code" ? [{ id: "work", label: "Work" }] : [],
      agent_capabilities: {
        permission_modes: [],
        effort_levels: [],
        supports_thread_delete: false,
        supports_thread_import_model: false,
        ...agentCaps,
      },
      transport_capabilities: transportCaps(true),
      capabilities: { supports_custom_cli_args: true, supports_config_overrides: false, ...flat },
    }) as unknown as BackendDescriptor;
  return [
    agent(
      "claude_code",
      "Claude Code",
      "claude_cli",
      ["claude_tty", "claude_cli", "tmux"],
      {
        permission_modes: [
          { id: "default", label: "Default" },
          { id: "auto", label: "Auto" },
        ],
        supports_thread_delete: true,
        supports_thread_import_model: true,
        supports_terminal_launch_model: true,
        supports_terminal_launch_effort: true,
      },
      { supports_thread_discovery: true },
    ),
    {
      ...agent("claude_tty", "Claude TUI", "claude_tty", ["claude_tty"], {}),
      transport_capabilities: transportCaps(true),
    } as BackendDescriptor,
    agent(
      "codex",
      "Codex",
      "codex_app_server",
      ["codex_app_server", "tmux"],
      {
        permission_modes: [
          { id: "default", label: "Default" },
          { id: "full_access", label: "Full Access" },
        ],
        supports_terminal_launch_effort: false,
      },
      { supports_config_overrides: true, supports_thread_discovery: true },
    ),
    agent(
      "opencode",
      "OpenCode",
      "opencode_http",
      ["opencode_http", "tmux"],
      { supports_terminal_launch_model: false, supports_terminal_launch_effort: false },
      { supports_thread_discovery: false },
    ),
    {
      ...agent("tmux", "Tmux", "tmux", ["tmux"], {}),
      transport_capabilities: transportCaps(false, true),
    } as BackendDescriptor,
  ];
}

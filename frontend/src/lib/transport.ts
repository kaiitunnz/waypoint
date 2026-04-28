import { SessionTransport } from "@/lib/types";

export function transportLabel(transport: SessionTransport): string {
  switch (transport) {
    case "codex_app_server":
      return "codex app server";
    case "claude_cli":
      return "claude cli";
    default:
      return "tmux";
  }
}

export function fidelityFor(transport: SessionTransport): "structured" | "heuristic" {
  if (transport === "codex_app_server" || transport === "claude_cli") {
    return "structured";
  }
  return "heuristic";
}

export function supportsResume(transport: SessionTransport): boolean {
  return transport === "tmux";
}

export function supportsStructuredApproval(transport: SessionTransport): boolean {
  return transport === "codex_app_server" || transport === "claude_cli";
}

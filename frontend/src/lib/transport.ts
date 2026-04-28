import { SessionTransport } from "@/lib/types";

export function transportLabel(transport: SessionTransport): string {
  return transport === "codex_app_server" ? "codex app server" : "tmux";
}

export function fidelityFor(transport: SessionTransport): "structured" | "heuristic" {
  return transport === "codex_app_server" ? "structured" : "heuristic";
}

export function supportsResume(transport: SessionTransport): boolean {
  return transport === "tmux";
}

export function supportsStructuredApproval(transport: SessionTransport): boolean {
  return transport === "codex_app_server";
}

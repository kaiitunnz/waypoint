"use client";

import {
  Dispatch,
  SetStateAction,
  useEffect,
  useMemo,
  useState,
} from "react";

import type { BackendCatalog } from "@/lib/backends";
import {
  agentTransports,
  defaultTransportFor,
  humaniseBackend,
  transportPresentation,
} from "@/lib/backends";
import { Backend, SessionTransport } from "@/lib/types";

interface AgentPickerProps {
  agents: Backend[];
  value: Backend;
  onChange: (backend: Backend) => void;
  catalog: BackendCatalog;
}

// Agent-primary launch selector: a native dropdown listing every registered
// agent by label. The chosen agent drives which transports the TransportPicker
// offers below it.
export function AgentPicker({ agents, value, onChange, catalog }: AgentPickerProps) {
  return (
    <label className="field">
      <span>Agent</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value as Backend)}
      >
        {agents.map((id) => (
          <option key={id} value={id}>
            {catalog.byId(id)?.label ?? humaniseBackend(id)}
          </option>
        ))}
      </select>
    </label>
  );
}

interface TransportPickerProps {
  transports: SessionTransport[];
  value: SessionTransport;
  onChange: (transport: SessionTransport) => void;
  catalog: BackendCatalog;
}

// Agent-primary transport selector: a native dropdown populated from an agent's
// supported_transports, with the selected transport's one-line description shown
// beneath as a field hint. Collapses to nothing when the agent exposes a single
// transport, since there is nothing to choose.
export function TransportPicker({
  transports,
  value,
  onChange,
  catalog,
}: TransportPickerProps) {
  if (transports.length <= 1) {
    return null;
  }
  const selected = transportPresentation(value, catalog);
  return (
    <label className="field">
      <span>Interface</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value as SessionTransport)}
      >
        {transports.map((transport) => (
          <option key={transport} value={transport}>
            {transportPresentation(transport, catalog).name}
          </option>
        ))}
      </select>
      <span className="muted field-hint">{selected.description}</span>
    </label>
  );
}

// Transport state that tracks the selected agent: it defaults to the agent's
// preferred transport and re-clamps whenever the agent changes so a transport
// carried over from another agent never sticks. Returns the live transport set
// alongside the value/setter so callers can gate agent-specific logic.
export function useTransportForAgent(
  backend: Backend,
  catalog: BackendCatalog,
): [SessionTransport, Dispatch<SetStateAction<SessionTransport>>, SessionTransport[]] {
  const transports = useMemo(
    () => agentTransports(backend, catalog),
    [backend, catalog],
  );
  const defaultTransport = defaultTransportFor(backend, catalog);
  const [transport, setTransport] = useState<SessionTransport>("");
  useEffect(() => {
    setTransport((current) =>
      transports.includes(current)
        ? current
        : (defaultTransport ?? transports[0] ?? ""),
    );
  }, [transports, defaultTransport]);
  return [transport, setTransport, transports];
}

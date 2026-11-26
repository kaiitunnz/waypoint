"use client";

import { Dispatch, SetStateAction, useState } from "react";

import type { BackendCatalog } from "@waypoint/web-core/backends";
import {
  agentTransports,
  humaniseBackend,
  transportPresentation,
} from "@waypoint/web-core/backends";
import { clampTransport } from "@waypoint/web-core/launch/form";
import { Backend, SessionTransport } from "@waypoint/web-core/types";

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

// Transport state that tracks the selected agent: the chosen transport while the
// agent offers it, else the agent's default.
export function useTransportForAgent(
  backend: Backend,
  catalog: BackendCatalog,
): [SessionTransport, Dispatch<SetStateAction<SessionTransport>>, SessionTransport[]] {
  const [chosen, setTransport] = useState<SessionTransport>("");
  return [clampTransport(chosen, backend, catalog), setTransport, agentTransports(backend, catalog)];
}

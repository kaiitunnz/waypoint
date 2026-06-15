"use client";

import {
  CSSProperties,
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

// Agent-primary launch selector: each registered agent rendered as a chip with
// its badge glyph and label. The chosen agent drives which transports the
// TransportPicker offers below it.
export function AgentPicker({ agents, value, onChange, catalog }: AgentPickerProps) {
  return (
    <div className="field agent-field">
      <span>Agent</span>
      <div className="agent-picker" role="radiogroup" aria-label="Agent">
        {agents.map((id) => {
          const descriptor = catalog.byId(id);
          const label = descriptor?.label ?? humaniseBackend(id);
          const glyph =
            descriptor?.badges?.glyph ?? label.slice(0, 1).toUpperCase();
          const color = descriptor?.badges?.color;
          const active = value === id;
          return (
            <button
              key={id}
              type="button"
              role="radio"
              aria-checked={active}
              className={`agent-option${active ? " active" : ""}`}
              style={
                color ? ({ "--agent-color": color } as CSSProperties) : undefined
              }
              onClick={() => onChange(id)}
            >
              <span className="agent-option-glyph" aria-hidden="true">
                {glyph}
              </span>
              <span className="agent-option-label">{label}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

interface TransportPickerProps {
  transports: SessionTransport[];
  value: SessionTransport;
  onChange: (transport: SessionTransport) => void;
  catalog: BackendCatalog;
}

// Agent-primary transport selector. Populated from an agent's
// supported_transports and rendered as a light segmented control: each
// transport gets a distinct icon and its user-facing name, with the selected
// transport's one-line description shown beneath. Collapses to nothing when the
// agent exposes a single transport, since there is nothing to choose.
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
    <div className="field transport-field">
      <span>Interface</span>
      <div
        className="segmented segmented-quiet transport-segmented"
        role="radiogroup"
        aria-label="Interface"
      >
        {transports.map((transport) => {
          const { name, description, kind } = transportPresentation(
            transport,
            catalog,
          );
          const active = value === transport;
          return (
            <button
              key={transport}
              type="button"
              role="radio"
              aria-checked={active}
              title={description}
              className={`segmented-item transport-segment${active ? " active" : ""}`}
              onClick={() => onChange(transport)}
            >
              <span className="transport-segment-icon" aria-hidden="true">
                <TransportGlyph transport={transport} kind={kind} />
              </span>
              {name}
            </button>
          );
        })}
      </div>
      <p className="transport-desc">{selected.description}</p>
    </div>
  );
}

interface AgentTransportPickerProps {
  agents: Backend[];
  agent: Backend;
  onAgentChange: (backend: Backend) => void;
  transport: SessionTransport;
  onTransportChange: (transport: SessionTransport) => void;
  catalog: BackendCatalog;
}

// The shared agent-primary launch control: an agent chip row followed by the
// transport segmented control for the chosen agent. New, Resume, and Schedule
// all render this so the launch vocabulary stays identical across panels.
export function AgentTransportPicker({
  agents,
  agent,
  onAgentChange,
  transport,
  onTransportChange,
  catalog,
}: AgentTransportPickerProps) {
  const transports = useMemo(
    () => agentTransports(agent, catalog),
    [agent, catalog],
  );
  return (
    <>
      <AgentPicker
        agents={agents}
        value={agent}
        onChange={onAgentChange}
        catalog={catalog}
      />
      <TransportPicker
        transports={transports}
        value={transport}
        onChange={onTransportChange}
        catalog={catalog}
      />
    </>
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

// Distinct glyph per transport: a speech bubble for the structured Chat
// adapter, an app window for the Emulated (real-app) tail, and a terminal
// prompt for the raw Terminal pane. Falls back to the kind for unknown
// transports.
function TransportGlyph({
  transport,
  kind,
}: {
  transport: SessionTransport;
  kind: "chat" | "terminal";
}) {
  if (transport === "claude_tty") return <EmulatedGlyph />;
  if (kind === "terminal") return <TerminalGlyph />;
  return <ChatGlyph />;
}

function ChatGlyph() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M3 2.9h10A1.3 1.3 0 0 1 14.3 4.2v4.9A1.3 1.3 0 0 1 13 10.4H6.7L4 12.8V10.4H3A1.3 1.3 0 0 1 1.7 9.1V4.2A1.3 1.3 0 0 1 3 2.9Z"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinejoin="round"
      />
      <path d="M4.3 5.4h7.4" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" opacity="0.65" />
      <path d="M4.3 7.5h4.8" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" opacity="0.65" />
    </svg>
  );
}

function EmulatedGlyph() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <rect
        x="1.7"
        y="2.7"
        width="12.6"
        height="10.6"
        rx="1.7"
        stroke="currentColor"
        strokeWidth="1.2"
      />
      <path d="M1.7 5.5h12.6" stroke="currentColor" strokeWidth="1.2" opacity="0.65" />
      <circle cx="3.8" cy="4.1" r="0.55" fill="currentColor" />
      <circle cx="5.6" cy="4.1" r="0.55" fill="currentColor" opacity="0.6" />
      <path d="M4.4 8h6.2M4.4 10.3h3.9" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" opacity="0.65" />
    </svg>
  );
}

function TerminalGlyph() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <rect
        x="1.6"
        y="2.6"
        width="12.8"
        height="10.8"
        rx="1.6"
        stroke="currentColor"
        strokeWidth="1.2"
      />
      <path
        d="M4.3 6.2l2 1.9-2 1.9"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path d="M7.8 10.2h3.6" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
    </svg>
  );
}

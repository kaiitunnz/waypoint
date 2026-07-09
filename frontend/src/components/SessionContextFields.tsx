"use client";

import { useMemo } from "react";

import { AccountProfilePicker } from "@/components/AccountProfilePicker";
import { AgentPicker, TransportPicker } from "@/components/AgentTransportPicker";
import type { BackendCatalog } from "@/lib/backends";
import { agentTransports } from "@/lib/backends";
import { AccountProfile, Backend, SessionTransport } from "@/lib/types";

interface SessionContextFieldsProps {
  agents: Backend[];
  agent: Backend;
  onAgentChange: (backend: Backend) => void;
  transport: SessionTransport;
  onTransportChange: (transport: SessionTransport) => void;
  accountProfiles: AccountProfile[];
  accountProfileId: string;
  onAccountProfileChange: (id: string) => void;
  // Non-null only when a remote/SSH launch target is active; shown as a compact
  // hint so the context reads "which host / account / interface".
  targetLabel: string | null;
  catalog: BackendCatalog;
}

// The top-of-form session context: agent, account profile (only when the agent
// exposes profiles), and interface — the choices that select the agent
// namespace, config root, credentials, and host path before any session
// subject or runtime tuning. Ordered agent -> account profile -> interface to
// match the settings-hierarchy dependency order.
export function SessionContextFields({
  agents,
  agent,
  onAgentChange,
  transport,
  onTransportChange,
  accountProfiles,
  accountProfileId,
  onAccountProfileChange,
  targetLabel,
  catalog,
}: SessionContextFieldsProps) {
  const transports = useMemo(
    () => agentTransports(agent, catalog),
    [agent, catalog],
  );
  return (
    <>
      <div className="field-grid-row">
        <AgentPicker
          agents={agents}
          value={agent}
          onChange={onAgentChange}
          catalog={catalog}
        />
        {accountProfiles.length > 0 ? (
          <AccountProfilePicker
            profiles={accountProfiles}
            value={accountProfileId}
            onChange={onAccountProfileChange}
          />
        ) : null}
        <TransportPicker
          transports={transports}
          value={transport}
          onChange={onTransportChange}
          catalog={catalog}
        />
      </div>
      {targetLabel ? (
        <p className="launch-section-hint">
          target: <strong>{targetLabel}</strong>
        </p>
      ) : null}
    </>
  );
}

"use client";

import { approvePlan } from "@waypoint/web-core/api";
import type { PlanViewModel } from "@waypoint/web-core/events";
import { planApprovalActions } from "@waypoint/web-core/session/approvals";
import type { SessionRecord } from "@waypoint/web-core/types";

import { useSessionContext } from "../SessionContext";
import { ClampedMarkdown } from "./ApprovalBody";
import { DecisionCard } from "./DecisionCard";

interface PlanApprovalDockProps {
  plan: PlanViewModel;
  onSession: (session: SessionRecord) => void;
}

// The plan the agent wants approved before it leaves plan mode.
export function PlanApprovalDock({ plan, onSession }: PlanApprovalDockProps) {
  const { host, token, sessionId, agentName } = useSessionContext();
  return (
    <DecisionCard
      key={plan.id}
      title={`Approve ${agentName}'s plan and exit plan mode`}
      copyText={plan.text}
      copyLabel="Copy plan"
      actions={planApprovalActions(plan.decisions)}
      supportsNote
      decide={async (decision, note) => {
        onSession(await approvePlan(host, token, sessionId, plan.id, decision, note));
      }}
    >
      <ClampedMarkdown text={plan.text} />
    </DecisionCard>
  );
}

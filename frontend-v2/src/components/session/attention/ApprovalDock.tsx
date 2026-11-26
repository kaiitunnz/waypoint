"use client";

import { useState } from "react";

import { approveSession } from "@waypoint/web-core/api";
import {
  approvalActions,
  approvalBody,
  approvalCopyText,
  approvalId,
  approvalPrompt,
} from "@waypoint/web-core/session/approvals";
import { pageOfKey, stepPage } from "@waypoint/web-core/session/paging";
import type { EventRecord } from "@waypoint/web-core/types";

import { useSessionContext } from "../SessionContext";
import { ApprovalBody } from "./ApprovalBody";
import { DecisionCard } from "./DecisionCard";
import { Pager } from "./Pager";

interface ApprovalDockProps {
  approvals: EventRecord[];
  decisions: readonly string[];
  supportsNote: boolean;
}

// The pager follows the opened approval, not its index, as earlier ones resolve.
const keyOf = (approval: EventRecord) => approvalId(approval) ?? String(approval.sequence);

export function ApprovalDock({ approvals, decisions, supportsNote }: ApprovalDockProps) {
  const { host, token, sessionId } = useSessionContext();
  const [openKey, setOpenKey] = useState<string | null>(null);
  const { count, page, current } = pageOfKey(approvals, openKey, keyOf);
  if (!current) return null;
  const body = approvalBody(current);
  const id = approvalId(current);

  return (
    <DecisionCard
      // Each approval starts unlocked and without a note.
      key={keyOf(current)}
      title={approvalPrompt(body) ?? "Approve this request"}
      pager={
        count > 1
          ? (locked) => (
              <Pager
                page={page}
                count={count}
                noun="approval"
                disabled={locked}
                onStep={(direction) => setOpenKey(keyOf(approvals[stepPage(page, direction, count)]))}
              />
            )
          : undefined
      }
      copyText={approvalCopyText(body, current.text)}
      copyLabel="Copy request"
      actions={approvalActions(decisions)}
      supportsNote={supportsNote}
      decide={(decision, note) => approveSession(host, token, sessionId, decision, note, id)}
    >
      <ApprovalBody body={body} />
    </DecisionCard>
  );
}

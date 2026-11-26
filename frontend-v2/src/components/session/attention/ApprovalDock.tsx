"use client";

import { useState } from "react";

import { approveSession } from "@waypoint/web-core/api";
import {
  approvalActions,
  approvalBody,
  approvalCopyText,
  approvalId,
  approvalPager,
  approvalPrompt,
  stepApprovalPage,
} from "@waypoint/web-core/session/approvals";
import type { EventRecord } from "@waypoint/web-core/types";

import { Icon } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";

import { useSessionContext } from "../SessionContext";
import { ApprovalBody } from "./ApprovalBody";
import styles from "./ApprovalDock.module.css";
import { DecisionCard } from "./DecisionCard";

interface ApprovalDockProps {
  approvals: EventRecord[];
  decisions: readonly string[];
  supportsNote: boolean;
}

// The tool approvals waiting on the human, one at a time with a pager.
export function ApprovalDock({ approvals, decisions, supportsNote }: ApprovalDockProps) {
  const { host, token, sessionId } = useSessionContext();
  const [pageIndex, setPageIndex] = useState(0);
  const { count, page, current } = approvalPager(approvals, pageIndex);
  if (!current) return null;
  const body = approvalBody(current);
  const id = approvalId(current);

  const pager =
    count > 1 ? (
      <span className={styles.pager}>
        <IconButton
          label="Previous approval"
          size="sm"
          disabled={page === 0}
          onClick={() => setPageIndex(stepApprovalPage(page, "prev", count))}
        >
          <Icon name="chevron-left" size={16} />
        </IconButton>
        <span className={styles.count}>
          {page + 1} / {count}
        </span>
        <IconButton
          label="Next approval"
          size="sm"
          disabled={page === count - 1}
          onClick={() => setPageIndex(stepApprovalPage(page, "next", count))}
        >
          <Icon name="chevron-right" size={16} />
        </IconButton>
      </span>
    ) : null;

  return (
    <DecisionCard
      // Each approval starts unlocked and without a note.
      key={id ?? current.sequence}
      title={approvalPrompt(body) ?? "Approve this request"}
      meta={pager}
      copyText={approvalCopyText(current)}
      copyLabel="Copy request"
      actions={approvalActions(decisions)}
      supportsNote={supportsNote}
      decide={(decision, note) => approveSession(host, token, sessionId, decision, note, id)}
    >
      <ApprovalBody body={body} />
    </DecisionCard>
  );
}

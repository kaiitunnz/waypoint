"use client";

import Link from "next/link";
import { useCallback, useMemo, useState } from "react";

import { gateBoardHref, gateView, isGateAnswered, type BoardGate } from "@waypoint/web-core/board";
import { useInboxItem } from "@waypoint/web-core/hooks/useInboxItem";
import { useNow } from "@waypoint/web-core/hooks/useNow";
import { formatInboxTime, requiredAnswersLeft } from "@waypoint/web-core/inbox";
import type { InboxBlock, InboxItem } from "@waypoint/web-core/types";

import { SessionContextProvider } from "@/components/session/SessionContext";
import { SourceLine } from "@/components/sessions/SourceLine";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Icon } from "@/components/ui/Icon";
import { StatusLamp } from "@/components/ui/StatusLamp";
import { cx } from "@/lib/cx";
import { handleAuthFailure } from "@/lib/auth";
import { usePointerUI } from "@/lib/layout";
import { useShellData } from "@/lib/shell-data";
import { useFailureToast } from "@/lib/useFailureToast";

import { DetailFrame, DetailState } from "./DetailFrame";
import { InboxBlockView } from "./InboxBlock";
import { useNeedsYou } from "./NeedsYouContext";
import styles from "./InboxDetail.module.css";

// The gate this item asks about, when a ticket is waiting on it.
function GateStrip({ gate }: { gate: BoardGate }) {
  const url = gate.ticket.pr_url;
  return (
    <div className={styles.gate}>
      <Icon name="board" size={14} />
      <span className={styles.gateKind}>{gateView(gate.ticket).gate}</span>
      {isGateAnswered(gate) ? <span>Answered</span> : null}
      <span className={styles.gateTitle}>{gate.ticket.title}</span>
      {url && /^https?:\/\//.test(url) ? (
        <a className={styles.gateLink} href={url} target="_blank" rel="noreferrer">
          Open PR
          <Icon name="external" size={14} />
        </a>
      ) : null}
      <Link className={styles.gateLink} href={gateBoardHref(gate)}>
        Open on board
        <Icon name="chevron-right" size={14} strokeWidth={2} />
      </Link>
    </div>
  );
}

// Runs of attachment blocks read as one group of files.
function groupBlocks(blocks: InboxBlock[]): InboxBlock[][] {
  const groups: InboxBlock[][] = [];
  for (const block of blocks) {
    const last = groups[groups.length - 1];
    if (block.type === "attachment" && last?.[0].type === "attachment") last.push(block);
    else groups.push([block]);
  }
  return groups;
}

function Blocks({ item }: { item: InboxItem }) {
  return (
    <div className={styles.blocks}>
      {groupBlocks(item.blocks).map((group) => (
        <div key={group[0].id} className={styles.group}>
          {group[0].type === "attachment" && group.length > 1 ? <p className={styles.files}>{group.length} files</p> : null}
          <div className={cx(group.length > 1 && styles.grid)}>
            {group.map((block) => (
              <InboxBlockView key={block.id} item={item} block={block} />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

export function InboxDetail({ id }: { id: string }) {
  const { host, token, gates } = useShellData();
  const { leave } = useNeedsYou();
  const pointer = usePointerUI();
  const now = useNow(30_000);
  const toastFailure = useFailureToast();
  const onRemoveError = useCallback((reason: unknown) => toastFailure(reason, "Could not delete"), [toastFailure]);
  const { item, gone, error, remove } = useInboxItem({
    host,
    token,
    itemId: id,
    onAuthFailure: handleAuthFailure,
    onRemoveError,
  });
  const [confirming, setConfirming] = useState(false);
  const gate = gates?.find((entry) => entry.item?.id === id);
  // Inbox text has no session behind it, so its paths stay text.
  const context = useMemo(
    () => ({
      host,
      token,
      sessionId: "",
      cwd: "",
      agentName: item?.from_label ?? "Agent",
      full: false,
      structured: true,
      openFiles: null,
    }),
    [host, token, item?.from_label],
  );

  if (gone) return <DetailState title="This item was deleted." />;
  if (error) return <DetailState title={error} />;
  if (!item) return <DetailState title="Loading…" />;

  const left = requiredAnswersLeft(item);
  const resolved = item.status === "resolved";
  const source = (
    <SourceLine
      lead={
        <span className={cx(styles.glyph, resolved && styles.done)} aria-hidden="true">
          <Icon name="mail" size={14} strokeWidth={2} />
        </span>
      }
      name={item.from_label ?? "unknown"}
      kind="Inbox"
      time={pointer ? undefined : <time dateTime={item.created_at}>{formatInboxTime(item.created_at, now)}</time>}
    />
  );

  return (
    <DetailFrame source={source} sessionId={item.from_session_id || undefined} onDelete={() => setConfirming(true)}>
      {pointer ? null : source}
      <h1 className={styles.subject}>{item.subject}</h1>
      <div className={styles.meta}>
        <span className={cx(styles.status, !resolved && styles.waiting)}>
          <StatusLamp state={resolved ? "idle" : "needs"} size="sm" flat />
          {resolved ? "Resolved" : "Open"}
        </span>
        <span>{item.read_at ? "Read" : "Unread"}</span>
        {pointer ? <time dateTime={item.created_at}>{formatInboxTime(item.created_at, now)}</time> : null}
        {left > 0 && !resolved ? <span>{left} answer{left > 1 ? "s" : ""} needed</span> : null}
      </div>
      {gate ? <GateStrip gate={gate} /> : null}
      <SessionContextProvider value={context}>
        <Blocks item={item} />
      </SessionContextProvider>
      <ConfirmDialog
        open={confirming}
        title="Delete inbox item?"
        detail="This cannot be undone."
        confirmLabel="Delete"
        onClose={() => setConfirming(false)}
        onConfirm={async () => {
          if (await remove()) leave();
        }}
      />
    </DetailFrame>
  );
}

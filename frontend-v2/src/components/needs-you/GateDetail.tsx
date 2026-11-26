"use client";

import Link from "next/link";

import { gateBoardHref, gateHeadline, gateKey, gateView } from "@waypoint/web-core/board";
import { useNow } from "@waypoint/web-core/hooks/useNow";
import { formatSessionTime } from "@waypoint/web-core/sessionPresentation";

import { SourceLine } from "@/components/sessions/SourceLine";
import { Icon } from "@/components/ui/Icon";
import { usePointerUI } from "@/lib/layout";
import { useShellData } from "@/lib/shell-data";

import { DetailFrame, DetailState } from "./DetailFrame";
import styles from "./GateDetail.module.css";

// A gate with no inbox item of its own: what is asked, and where to act.
export function GateDetail({ id }: { id: string }) {
  const { gates } = useShellData();
  const pointer = usePointerUI();
  const now = useNow(30_000);
  if (!gates) return <DetailState title="Loading…" />;
  const gate = gates.find((entry) => gateKey(entry) === id);
  if (!gate) return <DetailState title="No longer waiting" />;

  const { ticket } = gate;
  const time = ticket.awaiting_since ?? ticket.updated_at;
  const source = (
    <SourceLine
      lead={
        <span className={styles.glyph} aria-hidden="true">
          <Icon name="board" size={13} />
        </span>
      }
      name="Board"
      kind={gateView(ticket).gate}
      time={<time dateTime={time}>{formatSessionTime(time, now)}</time>}
    />
  );
  const url = ticket.pr_url;

  return (
    <DetailFrame source={source}>
      {pointer ? null : source}
      <h1 className={styles.headline}>{gateHeadline(ticket)}</h1>
      <p className={styles.title}>{ticket.title}</p>
      <p className={styles.summary}>{gateView(ticket).summary}</p>
      <div className={styles.links}>
        {url && /^https?:\/\//.test(url) ? (
          <a className={styles.link} href={url} target="_blank" rel="noreferrer">
            Open PR
            <Icon name="external" size={15} />
          </a>
        ) : null}
        <Link className={styles.link} href={gateBoardHref(gate)}>
          Open on board
          <Icon name="chevron-right" size={15} strokeWidth={2} />
        </Link>
      </div>
    </DetailFrame>
  );
}

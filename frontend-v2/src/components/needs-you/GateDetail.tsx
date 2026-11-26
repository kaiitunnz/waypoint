"use client";

import { gateHeadline, gateKey, gateTime, gateView } from "@waypoint/web-core/board";
import { useNow } from "@waypoint/web-core/hooks/useNow";
import { formatSessionTime } from "@waypoint/web-core/sessionPresentation";

import { SourceLine } from "@/components/sessions/SourceLine";
import { ActionChip } from "@/components/ui/ActionChip";
import { useShellData } from "@/lib/shell-data";

import { DetailFrame, DetailState } from "./DetailFrame";
import { GateLinks } from "./GateLinks";
import { SourceGlyph } from "./SourceGlyph";
import styles from "./GateDetail.module.css";

// A gate with no inbox item of its own.
export function GateDetail({ id }: { id: string }) {
  const { gates, gatesFailed, retryGates } = useShellData();
  const now = useNow(30_000);
  const gate = gates?.find((entry) => gateKey(entry) === id);
  // A gate that was not read may well be waiting still.
  if (!gate && gatesFailed) {
    return (
      <DetailState
        title={"Couldn't load board gates"}
        action={
          <ActionChip icon="refresh" onClick={retryGates}>
            Retry
          </ActionChip>
        }
      />
    );
  }
  if (!gates) return <DetailState title="Loading…" />;
  if (!gate) return <DetailState title="No longer waiting" />;

  const { ticket } = gate;
  const time = gateTime(gate);
  const source = (
    <SourceLine
      lead={<SourceGlyph icon="board" />}
      name="Board"
      kind={gateView(ticket).gate}
      time={<time dateTime={time}>{formatSessionTime(time, now)}</time>}
    />
  );
  return (
    <DetailFrame source={source}>
      <h1 className={styles.headline}>{gateHeadline(ticket)}</h1>
      <p className={styles.title}>{ticket.title}</p>
      <p className={styles.summary}>{gateView(ticket).summary}</p>
      <div className={styles.links}>
        <GateLinks gate={gate} linkClass={styles.link} />
      </div>
    </DetailFrame>
  );
}

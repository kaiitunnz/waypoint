import Link from "next/link";

import { gateBoardHref, gatePrUrl, type BoardGate } from "@waypoint/web-core/board";

import { Icon } from "@/components/ui/Icon";

// Where a gate is acted on: its pull request and its ticket on the board.
export function GateLinks({ gate, linkClass }: { gate: BoardGate; linkClass: string }) {
  const url = gatePrUrl(gate.ticket);
  return (
    <>
      {url ? (
        <a className={linkClass} href={url} target="_blank" rel="noreferrer">
          Open PR
          <Icon name="external" size={15} />
        </a>
      ) : null}
      <Link className={linkClass} href={gateBoardHref(gate)}>
        Open on board
        <Icon name="chevron-right" size={15} strokeWidth={2} />
      </Link>
    </>
  );
}

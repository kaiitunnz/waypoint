"use client";

import { parseNeedsId } from "@waypoint/web-core/needsYou";

import { AskDetail } from "./AskDetail";
import { GateDetail } from "./GateDetail";
import { InboxDetail } from "./InboxDetail";

// The detail of what a route id names: a pending ask, a board gate or an inbox item.
export function ItemDetail({ id }: { id: string }) {
  const target = parseNeedsId(id);
  switch (target.kind) {
    case "ask":
      return <AskDetail id={target.id} sessionId={target.sessionId} />;
    case "gate":
      return <GateDetail id={target.id} />;
    case "inbox":
      return <InboxDetail id={target.id} />;
  }
}

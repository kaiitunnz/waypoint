"use client";

import { parseNeedsId } from "@waypoint/web-core/needsYou";

import { AskDetail } from "./AskDetail";
import { GateDetail } from "./GateDetail";
import { InboxDetail } from "./InboxDetail";

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

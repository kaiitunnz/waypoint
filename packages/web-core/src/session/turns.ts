import type { TranscriptItem } from "./transcript";
import { cardKindOf, type CardKind } from "./cardKind";

// A turn is what the compact transcript spaces and routes together: the human's
// consecutive messages, the agent's consecutive messages and work, or one rule
// (a system note or model switch) standing between them.
export type TranscriptTurn =
  | { kind: "user"; key: string; items: TranscriptItem[] }
  // `routed`: the turn holds tool runs, tasks or asks, so the route line threads it.
  | { kind: "agent"; key: string; items: TranscriptItem[]; routed: boolean }
  | { kind: "rule"; key: string; item: TranscriptItem };

type Side = "user" | "agent" | "rule" | "none";

function isRoutedItem(item: TranscriptItem): boolean {
  return item.kind !== "single" || item.event.kind === "tool_call" || item.event.kind === "tool_result";
}

function sideOf(kind: CardKind): Side {
  switch (kind) {
    case "user_message":
    case "ask_answer":
      return "user";
    case "none":
      return "none";
    case "rule":
    case "model_switch":
      return "rule";
    default:
      return "agent";
  }
}

function itemSide(item: TranscriptItem): Side {
  switch (item.kind) {
    case "single":
      return sideOf(cardKindOf(item.event));
    case "pair":
      return sideOf(cardKindOf(item.pair.call ?? item.pair.result ?? item.event, item.pair));
    default:
      return "agent";
  }
}

// A stable React key for an entry: it survives later events being appended and
// streamed deltas being merged in (a merged event keeps its first id, not its sequence).
export function itemKey(item: TranscriptItem): string {
  switch (item.kind) {
    case "single":
      return item.event.id === undefined ? `seq-${item.event.sequence}` : `event-${item.event.id}`;
    case "pair":
      return `pair-${item.pair.itemId}`;
    default: {
      const first = item.items[0];
      return `${item.kind}-${first ? itemKey(first) : "empty"}`;
    }
  }
}

// Groups transcript items into turns, leaving out the ones that render nothing.
export function groupTurns(items: readonly TranscriptItem[]): TranscriptTurn[] {
  const turns: TranscriptTurn[] = [];
  for (const item of items) {
    const side = itemSide(item);
    if (side === "none") continue;
    if (side === "rule") {
      turns.push({ kind: "rule", key: itemKey(item), item });
      continue;
    }
    const last = turns[turns.length - 1];
    if (last && last.kind === side) {
      last.items.push(item);
      if (last.kind === "agent" && isRoutedItem(item)) last.routed = true;
      continue;
    }
    turns.push(
      side === "user"
        ? { kind: "user", key: itemKey(item), items: [item] }
        : { kind: "agent", key: itemKey(item), items: [item], routed: isRoutedItem(item) },
    );
  }
  return turns;
}

import { itemKey, type LeafItem, type TranscriptItem } from "./transcript";
import { cardKindOf, pairCardKindOf, type CardKind } from "./cardKind";

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
      return sideOf(pairCardKindOf(item.pair));
    default:
      return "agent";
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

// The calls a transcript item draws as a run line; a question or a sent file is a card.
export function runCallsOf(item: TranscriptItem): LeafItem[] {
  if (item.kind === "tool_run") return item.items;
  if (item.kind !== "pair") return [];
  const kind = pairCardKindOf(item.pair);
  return kind === "ask_question" || kind === "send_file" ? [] : [item];
}

// The turn's last tool run: the one a working session shows as live.
export function lastRunIndex(turn: TranscriptTurn): number {
  return turn.kind === "rule" ? -1 : turn.items.findLastIndex((item) => runCallsOf(item).length > 0);
}

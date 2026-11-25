import type { EventRecord } from "../types";
import { maxEventId } from "./stream";

export interface Echoes {
  // The texts of the human's messages that arrived after `afterId`.
  texts: string[];
  // The highest event id seen, to resume from.
  lastId: number | null;
}

export function userEchoes(events: readonly EventRecord[], afterId: number | null): Echoes {
  const texts: string[] = [];
  for (const event of events) {
    if (event.kind !== "user_input" || event.id === undefined) continue;
    if (afterId === null || event.id > afterId) texts.push(event.text);
  }
  return { texts, lastId: maxEventId(events, afterId) };
}

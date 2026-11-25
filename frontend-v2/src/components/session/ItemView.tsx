"use client";

import { attachmentSpecsFor } from "@waypoint/web-core/attachments";
import { parseTaskNotification, planTextForEvent } from "@waypoint/web-core/events";
import { cardKindOf, pairCardKindOf } from "@waypoint/web-core/session/cardKind";
import { modelChangeText } from "@waypoint/web-core/session/cardText";
import { summarizeNotificationRun } from "@waypoint/web-core/session/taskNotificationView";
import { primaryEvent, type LeafItem, type TranscriptItem } from "@waypoint/web-core/session/transcript";
import type { EventRecord } from "@waypoint/web-core/types";

import { AnswerCard } from "./cards/AnswerCard";
import { AskCard } from "./cards/AskCard";
import { Bubble } from "./cards/Bubble";
import { ChangesNote } from "./cards/ChangesNote";
import { HeuristicCard } from "./cards/HeuristicCard";
import { PlanCard } from "./cards/PlanCard";
import { Reasoning } from "./cards/Reasoning";
import { RunLine } from "./cards/RunLine";
import { SentFiles } from "./cards/SentFiles";
import { SystemRule } from "./cards/SystemRule";
import { TaskNote } from "./cards/TaskNote";
import { ToolRun } from "./cards/ToolRun";
import { useSessionContext } from "./SessionContext";

interface ItemViewProps {
  item: TranscriptItem;
  first: boolean;
  // This is the last run of a working session.
  live: boolean;
}

function NotificationRun({ items }: { items: LeafItem[] }) {
  const { full } = useSessionContext();
  const notes = items.flatMap((child) => {
    const event = primaryEvent(child);
    const view = parseTaskNotification(event);
    return view ? [{ event, view }] : [];
  });
  if (notes.length === 0) return null;
  const cards = notes.map(({ event, view }) => <TaskNote key={event.sequence} view={view} event={event} />);
  if (notes.length === 1) return cards[0];
  const summary = summarizeNotificationRun(notes.map(({ view }) => view.kind));
  return (
    <RunLine phrase={summary.phrase} glyphs={summary.glyphs} defaultOpen={full}>
      {cards}
    </RunLine>
  );
}

function Single({ event, first }: { event: EventRecord; first: boolean }) {
  const { full, structured } = useSessionContext();
  if (!structured) return <HeuristicCard event={event} />;
  switch (cardKindOf(event)) {
    case "user_message":
      return <Bubble tone="user" first={first} text={event.text} ts={event.ts} attachments={attachmentSpecsFor(event)} />;
    case "ask_answer":
      return <AnswerCard event={event} ts={event.ts} first={first} />;
    case "agent_message":
      return <Bubble tone="agent" first={first} text={event.text} ts={event.ts} attachments={attachmentSpecsFor(event)} />;
    case "status":
      return <Bubble tone="agent" first={first} text={event.text} ts={event.ts} />;
    case "reasoning":
      return <Reasoning event={event} />;
    case "ask_question":
      return <AskCard event={event} />;
    case "send_file":
      return <SentFiles event={event} />;
    case "todo":
    case "tool":
      return <ToolRun items={[{ kind: "single", event }]} working={false} />;
    case "plan": {
      const plan = planTextForEvent(event);
      return plan ? <PlanCard plan={plan} ts={event.ts} /> : null;
    }
    case "model_switch":
      return <SystemRule glyph="⇄" text={modelChangeText(event)} ts={event.ts} />;
    case "task_notification": {
      const view = parseTaskNotification(event);
      return view ? <TaskNote view={view} event={event} /> : null;
    }
    case "diff_note":
      return <ChangesNote event={event} defaultOpen={full} />;
    case "rule":
      return <SystemRule text={event.text} ts={event.ts} />;
    case "heuristic":
      return <HeuristicCard event={event} />;
    case "none":
      return null;
  }
}

export function ItemView({ item, first, live }: ItemViewProps) {
  switch (item.kind) {
    case "tool_run":
      return <ToolRun items={item.items} working={live} />;
    case "notification_run":
      return <NotificationRun items={item.items} />;
    case "pair":
      switch (pairCardKindOf(item.pair)) {
        case "ask_question":
          return item.pair.call ? <AskCard event={item.pair.call} /> : null;
        case "send_file":
          return <SentFiles event={primaryEvent(item)} />;
        default:
          return <ToolRun items={[item]} working={live} />;
      }
    case "single":
      return <Single event={item.event} first={first} />;
  }
}

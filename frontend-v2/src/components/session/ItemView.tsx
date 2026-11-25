"use client";

import { attachmentSpecsFor } from "@waypoint/web-core/attachments";
import { parseEvent, parseTaskNotification, planTextForEvent } from "@waypoint/web-core/events";
import { parseAskUserQuestion } from "@waypoint/web-core/session/askQuestion";
import { cardKindOf, pairCardKindOf } from "@waypoint/web-core/session/cardKind";
import { formatEventTime, modelChangeText } from "@waypoint/web-core/session/cardText";
import { summarizeNotificationRun } from "@waypoint/web-core/session/taskNotificationView";
import type { TranscriptItem } from "@waypoint/web-core/session/transcript";
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

type Leaf = Extract<TranscriptItem, { kind: "single" | "pair" }>;

export interface ItemViewProps {
  item: TranscriptItem;
  // The first item of its turn: a bubble here has the sharp origin corner.
  first: boolean;
  // Full density opens runs by themselves.
  full: boolean;
  // This is the last run of a working session.
  live: boolean;
  // Structured transports have cards; the rest show raw events.
  structured: boolean;
}

function NotificationRun({ items, full }: { items: Leaf[]; full: boolean }) {
  const notes = items.flatMap((child) => {
    const event = child.kind === "pair" ? (child.pair.call ?? child.pair.result ?? child.event) : child.event;
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

function Single({ event, first, full, structured }: { event: EventRecord; first: boolean; full: boolean; structured: boolean }) {
  const { modelOptions } = useSessionContext();
  const time = formatEventTime(event.ts);
  if (!structured) return <HeuristicCard event={event} />;
  switch (cardKindOf(event)) {
    case "user_message":
      return <Bubble tone="user" first={first} text={event.text} time={time} attachments={attachmentSpecsFor(event)} />;
    case "ask_answer":
      return <AnswerCard event={event} time={time} first={first} />;
    case "agent_message":
      return <Bubble tone="agent" first={first} text={event.text} time={time} attachments={attachmentSpecsFor(event)} />;
    case "status":
      return <Bubble tone="agent" first={first} text={event.text} time={time} />;
    case "reasoning":
      return <Reasoning event={event} />;
    case "ask_question":
      return <AskCard event={event} questions={parseAskUserQuestion(event) ?? []} />;
    case "send_file":
      return <SentFiles event={event} />;
    case "todo":
    case "tool":
      return <ToolRun items={[{ kind: "single", event }]} defaultOpen={full} working={false} />;
    case "plan": {
      const plan = planTextForEvent(event);
      return plan ? <PlanCard plan={plan} ts={event.ts} /> : null;
    }
    case "model_switch":
      return <SystemRule glyph="⇄" text={modelChangeText(event, modelOptions)} time={time} />;
    case "task_notification": {
      const view = parseTaskNotification(event);
      return view ? <TaskNote view={view} event={event} /> : null;
    }
    case "diff_note": {
      const preview = parseEvent(event).diffPreview;
      return preview ? <ChangesNote event={event} preview={preview} defaultOpen={full} /> : null;
    }
    case "rule":
      return <SystemRule text={event.text} time={time} />;
    case "heuristic":
      return <HeuristicCard event={event} />;
    case "none":
      return null;
  }
}

// One entry of the transcript as its card.
export function ItemView({ item, first, full, live, structured }: ItemViewProps) {
  switch (item.kind) {
    case "tool_run":
      return <ToolRun items={item.items} defaultOpen={full} working={live} />;
    case "notification_run":
      return <NotificationRun items={item.items} full={full} />;
    case "pair": {
      const call = item.pair.call;
      switch (pairCardKindOf(item.pair)) {
        case "ask_question":
          return call ? <AskCard event={call} questions={parseAskUserQuestion(call) ?? []} /> : null;
        case "send_file": {
          const event = call ?? item.pair.result;
          return event ? <SentFiles event={event} /> : null;
        }
        default:
          return <ToolRun items={[item]} defaultOpen={full} working={live} />;
      }
    }
    case "single":
      return <Single event={item.event} first={first} full={full} structured={structured} />;
  }
}

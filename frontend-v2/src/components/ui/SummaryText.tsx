import { summaryParts } from "@waypoint/web-core/session/summary";

import { InlineCode } from "./InlineCode";

// A call's summary, with its machine strings in mono.
export function SummaryText({ text }: { text: string }) {
  return summaryParts(text).map((part, index) => (part.code ? <InlineCode key={index}>{part.text}</InlineCode> : part.text));
}

// A title that is the agent's summary reads its backticks; any other is as written.
export function TitleText({ title }: { title: { text: string; marked: boolean } }) {
  return title.marked ? <SummaryText text={title.text} /> : title.text;
}

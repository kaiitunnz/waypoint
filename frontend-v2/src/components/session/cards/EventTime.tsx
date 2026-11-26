import { formatEventTime } from "@waypoint/web-core/session/cardText";

// A time, or the range up to `until` when that reads differently.
export function EventTime({ ts, until, className }: { ts: string; until?: string; className?: string }) {
  const from = formatEventTime(ts);
  const to = until ? formatEventTime(until) : from;
  if (!until || to === from) {
    return (
      <time className={className} dateTime={ts}>
        {from}
      </time>
    );
  }
  return (
    <span className={className}>
      <time dateTime={ts}>{from}</time>–<time dateTime={until}>{to}</time>
    </span>
  );
}

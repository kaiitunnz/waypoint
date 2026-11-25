import { formatEventTime } from "@waypoint/web-core/session/cardText";

export function EventTime({ ts, className }: { ts: string; className?: string }) {
  return (
    <time className={className} dateTime={ts}>
      {formatEventTime(ts)}
    </time>
  );
}

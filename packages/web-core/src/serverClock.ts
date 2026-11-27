// The server's clock against this client's. A server timestamp is compared with
// serverNow(), never with Date.now(), which a skewed clock on either side breaks.

// A `Date` header drops the milliseconds and uvicorn rewrites it once a second, so the
// server's clock reads up to this much later than the header says.
const DATE_LAG_MS = 2_000;
// A slower answer says too little about the clocks to replace the offset held.
const MAX_ROUND_TRIP_MS = 5_000;

// The server's clock minus this client's, in ms.
let offset = 0;
let measured: string | null = null;
const listeners = new Set<() => void>();
const inFlight = new Map<string, Promise<void>>();

export function serverClockOffset(): number {
  return offset;
}

// A time on this client's clock, on the server's.
export function toServerTime(clientMs: number): number {
  return clientMs + offset;
}

export function serverNow(): number {
  return toServerTime(Date.now());
}

export function subscribeServerClock(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

// The offset one `Date` header shows, from a request sent and answered at these client
// times; 0 where it cannot tell the clocks apart, and null where there is no header.
export function estimateClockOffset(date: string | null, sentAt: number, receivedAt: number): number | null {
  const header = date ? Date.parse(date) : Number.NaN;
  if (!Number.isFinite(header)) return null;
  const least = header - receivedAt;
  const most = header + DATE_LAG_MS - sentAt;
  if (least <= 0 && most >= 0) return 0;
  // The later reading, so a time the server has just written never reads as the future.
  return Math.round(header + DATE_LAG_MS - (sentAt + receivedAt) / 2);
}

function setOffset(next: number): void {
  if (next === offset) return;
  offset = next;
  for (const listener of listeners) listener();
}

async function measure(host: string): Promise<void> {
  const sentAt = Date.now();
  let date: string | null;
  try {
    const response = await fetch(`${host}/health`, {
      cache: "no-store",
      signal: AbortSignal.timeout(MAX_ROUND_TRIP_MS),
    });
    date = response.headers.get("date");
  } catch {
    return;
  }
  const receivedAt = Date.now();
  // A suspended page's timer can let an answer through long after the timeout.
  if (receivedAt - sentAt > MAX_ROUND_TRIP_MS) return;
  // Another host was connected to meanwhile.
  if (measured !== host) return;
  // A server that does not expose its `Date` is taken to agree.
  setOffset(estimateClockOffset(date, sentAt, receivedAt) ?? 0);
}

// Measures the offset against `host`; called when a connection to it opens.
export function syncServerClock(host: string): Promise<void> {
  if (measured !== host) {
    measured = host;
    setOffset(0);
  }
  const running = inFlight.get(host);
  if (running) return running;
  const next = measure(host).finally(() => inFlight.delete(host));
  inFlight.set(host, next);
  return next;
}

// Compare a server timestamp with serverNow(), never Date.now(): the two clocks can
// disagree.

// A `Date` header drops the milliseconds and uvicorn rewrites it once a second, so the
// server's clock reads up to this much later than the header says.
const DATE_LAG_MS = 2_000;
// A slower answer says too little about the clocks to replace the offset held.
const MAX_ROUND_TRIP_MS = 5_000;

// The server's clock minus this client's, in ms.
let offset = 0;
// What the headers read from the host so far allow the offset to be.
let bounds: { least: number; most: number } | null = null;
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

function setOffset(next: number): void {
  if (next === offset) return;
  offset = next;
  for (const listener of listeners) listener();
}

// Takes in the `Date` header of a request sent and answered at these client times, and
// gives the offset the headers so far show.
function readDate(date: string | null, sentAt: number, receivedAt: number): number {
  const header = date ? Date.parse(date) : Number.NaN;
  // A server that does not expose its `Date` is taken to agree.
  if (!Number.isFinite(header)) return 0;
  let least = header - receivedAt;
  let most = header + DATE_LAG_MS - sentAt;
  // Headers narrow one another while they overlap; one that does not means a clock moved.
  if (bounds && least <= bounds.most && most >= bounds.least) {
    least = Math.max(least, bounds.least);
    most = Math.min(most, bounds.most);
  }
  bounds = { least, most };
  if (least <= 0 && most >= 0) return 0;
  // The latest the server can be, so a time it has just written never reads as the future.
  return most;
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
  setOffset(readDate(date, sentAt, receivedAt));
}

export function syncServerClock(host: string): Promise<void> {
  if (measured !== host) {
    measured = host;
    bounds = null;
    setOffset(0);
  }
  const running = inFlight.get(host);
  if (running) return running;
  const next = measure(host).finally(() => inFlight.delete(host));
  inFlight.set(host, next);
  return next;
}

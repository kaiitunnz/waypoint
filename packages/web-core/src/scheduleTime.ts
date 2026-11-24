/** Clock and countdown formatting for scheduled sessions and messages. */

export function formatClock(target: Date): string {
  const sameDay = new Date().toDateString() === target.toDateString();
  return target.toLocaleString(undefined, {
    month: sameDay ? undefined : "short",
    day: sameDay ? undefined : "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function formatRelative(target: Date): string {
  const diff = target.getTime() - Date.now();
  if (diff <= 0) {
    return "any moment";
  }
  const minutes = Math.round(diff / 60_000);
  if (minutes < 1) {
    return "in <1m";
  }
  if (minutes < 60) {
    return `in ${minutes}m`;
  }
  const hours = Math.round(minutes / 60);
  if (hours < 48) {
    return `in ${hours}h`;
  }
  const days = Math.round(hours / 24);
  return `in ${days}d`;
}

"use client";

const HOST_KEY = "waypoint.host";
const TOKEN_KEY = "waypoint.token";

export function readHost(): string {
  if (typeof window === "undefined") {
    return "";
  }
  const saved = window.localStorage.getItem(HOST_KEY);
  const inferred = inferBackendHost();
  if (!saved) {
    return inferred;
  }
  if (shouldReplaceSavedHost(saved, inferred)) {
    return inferred;
  }
  return saved;
}

export function writeHost(host: string): void {
  window.localStorage.setItem(HOST_KEY, host);
}

export function readToken(): string {
  if (typeof window === "undefined") {
    return "";
  }
  return window.localStorage.getItem(TOKEN_KEY) ?? "";
}

export function writeToken(token: string): void {
  window.localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  window.localStorage.removeItem(TOKEN_KEY);
}

function inferBackendHost(): string {
  const protocol = window.location.protocol === "https:" ? "https:" : "http:";
  const hostname = window.location.hostname || "127.0.0.1";
  return `${protocol}//${hostname}:8787`;
}

function shouldReplaceSavedHost(saved: string, inferred: string): boolean {
  try {
    const savedUrl = new URL(saved);
    const inferredUrl = new URL(inferred);
    const savedIsLoopback = savedUrl.hostname === "127.0.0.1" || savedUrl.hostname === "localhost";
    const inferredIsRemote = inferredUrl.hostname !== "127.0.0.1" && inferredUrl.hostname !== "localhost";
    return savedIsLoopback && inferredIsRemote;
  } catch {
    return false;
  }
}

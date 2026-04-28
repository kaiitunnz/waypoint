"use client";

const HOST_KEY = "waypoint.host";
const TOKEN_KEY = "waypoint.token";

export function readHost(): string {
  if (typeof window === "undefined") {
    return "";
  }
  return window.localStorage.getItem(HOST_KEY) ?? "http://127.0.0.1:8787";
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

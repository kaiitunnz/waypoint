"use client";

import { useSyncExternalStore } from "react";

import { clearToken, readHost, readToken, writeHost, writeToken } from "@waypoint/web-core/store";

export interface AuthState {
  // False until the browser has been read; the server render and hydration show nothing.
  ready: boolean;
  // The active backend URL, port included (`waypoint.host`).
  host: string;
  token: string;
  // A line the login screen shows once: why the person is signed out.
  notice: string;
  // What the host picked in the switcher was called there, until the next sign-in.
  hostName: string;
}

const SERVER_STATE: AuthState = { ready: false, host: "", token: "", notice: "", hostName: "" };

let notice = "";
let hostName = "";
let cached: AuthState = SERVER_STATE;
const listeners = new Set<() => void>();

// web-core's store writes fire no same-tab event, so every write goes through
// here and notifies. The snapshot is rebuilt only when a value changed, which
// keeps it referentially stable for useSyncExternalStore.
function getSnapshot(): AuthState {
  const host = readHost();
  const token = readToken();
  if (
    !cached.ready ||
    cached.host !== host ||
    cached.token !== token ||
    cached.notice !== notice ||
    cached.hostName !== hostName
  ) {
    cached = { ready: true, host, token, notice, hostName };
  }
  return cached;
}

function emit() {
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  window.addEventListener("storage", emit);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", emit);
  };
}

export function signIn(host: string, token: string) {
  writeHost(host);
  writeToken(token);
  notice = "";
  hostName = "";
  emit();
}

// A token the backend no longer accepts: back to the login screen, on the same URL.
export function handleAuthFailure() {
  clearToken();
  notice = "Session expired. Log in again.";
  hostName = "";
  emit();
}

// Another backend needs its own sign-in.
export function switchHost(host: string, name = "") {
  writeHost(host);
  clearToken();
  notice = "Switched backend. Log in to continue.";
  hostName = name;
  emit();
}

export function useAuth(): AuthState {
  return useSyncExternalStore(subscribe, getSnapshot, () => SERVER_STATE);
}

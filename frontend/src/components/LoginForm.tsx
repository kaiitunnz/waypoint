"use client";

import { FormEvent, useState } from "react";

interface LoginFormProps {
  defaultHost: string;
  onSubmit: (host: string, password: string) => Promise<void>;
}

export function LoginForm({ defaultHost, onSubmit }: LoginFormProps) {
  const [host, setHost] = useState(defaultHost);
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await onSubmit(host, password);
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="panel stack" onSubmit={handleSubmit}>
      <div>
        <h2>Connect to Waypoint</h2>
        <p className="muted">Enter the Tailscale-reachable backend URL and password.</p>
      </div>
      <label className="field">
        <span>Backend URL</span>
        <input value={host} onChange={(event) => setHost(event.target.value)} placeholder="http://100.x.y.z:8787" />
      </label>
      <label className="field">
        <span>Password</span>
        <input
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          placeholder="Waypoint password"
        />
      </label>
      {error ? <p className="error">{error}</p> : null}
      <button className="primary" disabled={busy} type="submit">
        {busy ? "Connecting..." : "Connect"}
      </button>
    </form>
  );
}

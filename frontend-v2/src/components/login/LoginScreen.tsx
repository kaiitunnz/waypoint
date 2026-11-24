"use client";

import { useMemo, useRef, useState } from "react";

import { login } from "@waypoint/web-core/api";
import { useBackendProbe } from "@waypoint/web-core/hooks/useBackendProbe";
import { useTailnetSnapshot } from "@waypoint/web-core/hooks/useTailnetSnapshot";
import {
  buildBackendUrl,
  isCompleteHost,
  parsePort,
  splitBackendUrl,
  type TypedAddress,
} from "@waypoint/web-core/backendAddress";
import {
  buildHostGroups,
  describeHost,
  pageDevice,
  rowKeyFor,
  type HostRow,
} from "@waypoint/web-core/hostPicker";
import { readRecentHosts, writeRecentHost } from "@waypoint/web-core/recentHosts";
import { tailnetSummary, type TailnetSnapshot } from "@waypoint/web-core/tailnet";

import { ThemeToggle } from "@/components/shell/ThemeToggle";
import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";
import { Logo } from "@/components/ui/Logo";
import { signIn } from "@/lib/auth";
import { useDesktop } from "@/lib/layout";

import { HostPicker } from "@/components/hosts/HostPicker";
import { SignInForm } from "./SignInForm";
import styles from "./LoginScreen.module.css";

type Choice = TypedAddress & { name: string };

function choiceFromUrl(url: string, name: string): Choice | null {
  const address = splitBackendUrl(url);
  return address
    ? { name, protocol: address.protocol, hostname: address.hostname, portText: String(address.port) }
    : null;
}

function TailscaleSummary({ snapshot }: { snapshot: TailnetSnapshot | null }) {
  const text = tailnetSummary(snapshot);
  return text ? (
    <span className={styles.summary}>
      <Icon name="tailscale" size={13} />
      {text}
    </span>
  ) : null;
}

interface LoginScreenProps {
  // The saved or inferred backend URL: the starting choice.
  defaultHost: string;
  // What the host was called where it was picked; else its name is worked out from the address.
  hostName: string;
  notice: string;
}

export function LoginScreen({ defaultHost, hostName, notice }: LoginScreenProps) {
  const desktop = useDesktop();
  const [recents] = useState(readRecentHosts);
  const { snapshot, loading, refresh } = useTailnetSnapshot({ kind: "local" });
  const [query, setQuery] = useState("");
  const [choice, setChoice] = useState(() =>
    choiceFromUrl(defaultHost, hostName || describeHost(defaultHost, { recents })),
  );
  const [step, setStep] = useState<"hosts" | "password">(notice && choice ? "password" : "hosts");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);

  const groups = useMemo(
    () =>
      buildHostGroups({
        ...pageDevice(),
        snapshot,
        recents,
        query,
      }),
    [snapshot, recents, query],
  );

  const port = choice ? parsePort(choice.portText) : null;
  const url = choice && port !== null ? buildBackendUrl({ ...choice, port }) : null;
  const probeable = choice !== null && isCompleteHost(choice.hostname);
  // The only probe: the chosen host and port, once its sign-in form is on screen.
  const probe = useBackendProbe(probeable && (desktop || step === "password") ? url : null);

  const setPortText = (portText: string) => setChoice((current) => current && { ...current, portText });

  const select = (row: HostRow) => {
    const next = choiceFromUrl(row.url, row.name);
    if (!next) return;
    setChoice(next);
    setPassword("");
    setError("");
    setStep("password");
  };

  const typed = (address: TypedAddress | null) => {
    if (!address) return;
    if (address.hostname !== choice?.hostname) setPassword("");
    setChoice({ name: address.hostname, ...address });
    setError("");
    if (!desktop) setStep("password");
  };

  const changeHost = () => {
    if (desktop) {
      searchRef.current?.focus();
    } else {
      setStep("hosts");
    }
  };

  async function submit() {
    if (!choice || port === null || !url) return;
    setBusy(true);
    setError("");
    try {
      const token = await login(url, password);
      writeRecentHost({ url, name: choice.name });
      signIn(url, token);
    } catch (failure) {
      setError(
        failure instanceof TypeError
          ? `Could not reach ${url}`
          : failure instanceof Error
            ? failure.message
            : "Login failed",
      );
      setBusy(false);
    }
  }

  const picker = (
    <HostPicker
      groups={groups}
      query={query}
      onQueryChange={setQuery}
      // On mobile a pick moves straight to the password screen, so only desktop shows it.
      selectedKey={desktop && url ? rowKeyFor(groups, url) : null}
      onSelect={select}
      onAddress={typed}
      liveAddress={desktop}
      port={desktop && choice ? { text: choice.portText, onChange: setPortText } : undefined}
      searchRef={searchRef}
      headingLevel="h2"
    />
  );
  const form = choice ? (
    <SignInForm
      name={choice.name}
      protocol={choice.protocol}
      hostname={choice.hostname}
      portText={choice.portText}
      portValid={port !== null}
      onPortChange={setPortText}
      onChangeHost={changeHost}
      probe={probe}
      password={password}
      onPasswordChange={setPassword}
      onSubmit={submit}
      busy={busy}
      error={error}
    />
  ) : null;
  const noticeLine = notice ? (
    <p className={styles.notice} role="status">
      {notice}
    </p>
  ) : null;

  if (desktop) {
    return (
      <main className={styles.page}>
        <section className={styles.card} aria-label="Connect to a host">
          <header className={styles.cardHead}>
            <Logo size={30} />
            <h1 className={styles.cardTitle}>Choose a host</h1>
            <TailscaleSummary snapshot={snapshot} />
            <span className={styles.grow} />
            <Button variant="text" size="sm" onClick={refresh} disabled={loading}>
              <Icon name="refresh" size={15} />
              Refresh
            </Button>
            <ThemeToggle />
          </header>
          <div className={styles.cols}>
            <div className={styles.listPane}>
              {noticeLine}
              {picker}
            </div>
            <div className={styles.passPane}>
              <h2 className={styles.signin}>Sign in</h2>
              {form}
            </div>
          </div>
        </section>
      </main>
    );
  }

  if (step === "password" && choice) {
    return (
      <main className={styles.screen}>
        <header className={styles.top}>
          <button type="button" className={styles.back} onClick={() => setStep("hosts")}>
            <Icon name="chevron-left" size={20} />
            Hosts
          </button>
          <span className={styles.grow} />
          <ThemeToggle />
        </header>
        <div className={styles.passScreen}>
          <h1 className={styles.signinTitle}>Sign in</h1>
          {noticeLine}
          {form}
        </div>
      </main>
    );
  }

  return (
    <main className={styles.screen}>
      <header className={styles.top}>
        <div className={styles.heading}>
          <div className={styles.titleRow}>
            <Logo size={26} />
            <h1 className={styles.title}>Choose a host</h1>
          </div>
          <TailscaleSummary snapshot={snapshot} />
        </div>
        <IconButton label="Refresh hosts" onClick={refresh} disabled={loading}>
          <Icon name="refresh" size={19} />
        </IconButton>
        <ThemeToggle />
      </header>
      {noticeLine}
      {picker}
    </main>
  );
}

"use client";

import { useRouter } from "next/navigation";
import { useCallback, useMemo, useRef, useState, type KeyboardEvent } from "react";

import { attachTmux, createSchedule, createSession } from "@waypoint/web-core/api";
import type { LaunchContext } from "@waypoint/web-core/hooks/useLaunchContext";
import { useLaunchForm } from "@waypoint/web-core/hooks/useLaunchForm";
import { attachRequest, attachTargetError } from "@waypoint/web-core/launch/attach";
import { classifyLaunchFailure } from "@waypoint/web-core/launch/failure";
import { openingPreset, type PresetSelection } from "@waypoint/web-core/launch/presets";
import { withScheduleTiming } from "@waypoint/web-core/launch/schedule";
import { sessionHref } from "@waypoint/web-core/shellNav";
import { pushRecentCwd, readRecentCwds } from "@waypoint/web-core/store";

import { SshConnectDialog } from "@/components/hosts/SshConnectDialog";
import { ActionChip } from "@/components/ui/ActionChip";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Icon } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { useToast } from "@/components/ui/Toast";
import { handleAuthFailure } from "@/lib/auth";
import { cx } from "@/lib/cx";
import { useMultiColumn, usePointerUI } from "@/lib/layout";
import { useShellData } from "@/lib/shell-data";
import { useMountedRef } from "@/lib/useMountedRef";

import { AttachPane, type AttachState } from "./AttachPane";
import { LaunchFields } from "./LaunchFields";
import { PresetDialogs, PresetRow, PresetsColumn } from "./Presets";
import { ResumePane } from "./ResumePane";
import { initialScheduleState, ScheduleWhen, type ScheduleState } from "./ScheduleWhen";
import { TargetChip } from "./TargetChip";
import { usePresetControls } from "./usePresetControls";
import { useResume } from "./useResume";
import styles from "./LaunchDialog.module.css";

export type LaunchMode = "new" | "resume" | "attach" | "schedule";

export interface LaunchRequest {
  mode: LaunchMode;
  preset: PresetSelection;
}

const TITLES: Record<LaunchMode, string> = {
  new: "New session",
  resume: "Resume a thread",
  attach: "Attach a pane",
  schedule: "Schedule a session",
};

const MODE_LABELS: Record<LaunchMode, [pointer: string, touch: string]> = {
  new: ["New", "New"],
  resume: ["Resume thread", "Resume"],
  attach: ["Attach tmux pane", "Attach"],
  schedule: ["Schedule", "Schedule"],
};

const MODES: LaunchMode[] = ["new", "resume", "attach", "schedule"];

const PRIMARY: Record<LaunchMode, string> = {
  new: "Launch",
  resume: "Resume",
  attach: "Attach",
  schedule: "Schedule",
};

interface LaunchDialogProps {
  request: LaunchRequest | null;
  launch: LaunchContext;
  onClose: () => void;
}

// Launch, resume, attach or schedule a session. The form starts fresh on each open.
export function LaunchDialog({ request, launch, onClose }: LaunchDialogProps) {
  if (!request) return null;
  if (!launch.ready) {
    return (
      <Dialog open title={TITLES[request.mode]} onClose={onClose}>
        <p className={styles.loading}>Loading…</p>
      </Dialog>
    );
  }
  return <LaunchSurface request={request} launch={launch} onClose={onClose} />;
}

function LaunchSurface({ request, launch, onClose }: { request: LaunchRequest; launch: LaunchContext; onClose: () => void }) {
  const { host, token, launchTargets } = useShellData();
  const router = useRouter();
  const { toast } = useToast();
  const pointer = usePointerUI();
  const wide = useMultiColumn();
  const { catalog, defaults } = launch;
  const targetId = launchTargets.activeTargetId;
  const activeTarget = launchTargets.targets.find((target) => target.id === targetId) ?? null;
  const [mode, setMode] = useState<LaunchMode>(request.mode);

  const [opening] = useState(() => openingPreset(launch.presets, launch.defaultPresetId, request.preset));
  const form = useLaunchForm({
    host,
    token,
    onAuthFailure: handleAuthFailure,
    fetchModels: mode === "new" || mode === "schedule",
    launchableBackends: defaults.launchableBackends,
    defaultBackend: defaults.defaultBackend,
    defaultCwd: defaults.defaultCwd,
    launchTargetId: targetId || null,
    defaultLaunchEnvByBackend: defaults.defaultLaunchEnvByBackend,
    accountProfilesByBackend: defaults.accountProfilesByBackend,
    usageProviderOptions: launch.usageProviderOptions,
    catalog,
    initialPreset: opening.spec,
  });
  const presets = usePresetControls(launch, form, opening.id, targetId);

  const recents = useMemo(() => readRecentCwds(host, targetId), [host, targetId]);

  const [busy, setBusy] = useState(false);
  // What went wrong; a failed launch can be retried, a failed list or delete only dismissed.
  const [failure, setFailure] = useState<{ message: string; retry: boolean } | null>(null);
  const reportError = useCallback((message: string) => setFailure({ message, retry: false }), []);
  const [rejectedCwd, setCwdError] = useState<string | null>(null);
  // The folder error belongs to the folder the server rejected.
  const cwdError = rejectedCwd !== null && form.cwd === rejectedCwd ? rejectedCwd : null;
  const [sshRetry, setSshRetry] = useState<(() => void) | null>(null);
  const [attach, setAttach] = useState<AttachState>({ target: "", agent: form.backend, title: "" });
  const [schedule, setSchedule] = useState<ScheduleState>(initialScheduleState);
  // Keeps the state object when nothing changes, so a repeated report renders nothing.
  const updateSchedule = useCallback(
    (patch: Partial<ScheduleState>) =>
      setSchedule((current) =>
        (Object.keys(patch) as (keyof ScheduleState)[]).every((key) => current[key] === patch[key])
          ? current
          : { ...current, ...patch },
      ),
    [],
  );
  const resume = useResume({
    host,
    token,
    enabled: mode === "resume",
    catalog,
    launchable: defaults.launchableBackends,
    launchTargetId: targetId,
    formBackend: form.backend,
    formAccountProfileId: form.accountProfileId,
    defaultLaunchEnvByBackend: defaults.defaultLaunchEnvByBackend,
    revision: launchTargets.targets,
    onError: reportError,
  });

  const changeMode = (next: LaunchMode) => {
    setMode(next);
    setFailure(null);
    setCwdError(null);
  };

  // A password-auth target that is not connected asks for its password, then
  // replays the request. With `folder.field`, a rejected cwd marks the folder
  // field instead of the notice.
  async function run(action: () => Promise<void>, fallback: string, folder?: { cwd: string; field?: boolean }) {
    setBusy(true);
    setFailure(null);
    try {
      await action();
    } catch (error) {
      const outcome = classifyLaunchFailure(error, fallback);
      if (outcome.kind === "auth") handleAuthFailure();
      else if (outcome.kind === "cwd" && folder?.field) setCwdError(folder.cwd);
      else if (outcome.kind === "cwd") setFailure({ message: folder ? `Folder not found: ${folder.cwd}` : "Folder not found", retry: true });
      else if (outcome.kind === "ssh" && activeTarget) setSshRetry(() => () => void run(action, fallback, folder));
      else setFailure({ message: outcome.message, retry: true });
    } finally {
      setBusy(false);
    }
  }

  // A launch cancelled while in flight stays where the person is.
  const mounted = useMountedRef();
  function open(sessionId: string) {
    if (!mounted.current) return;
    onClose();
    router.push(sessionHref(sessionId));
  }

  function submit() {
    if (busy) return;
    if (mode === "new") {
      const request = form.createRequest(presets.selectedId);
      void run(
        async () => {
          const session = await createSession(host, token, request);
          pushRecentCwd(host, targetId, request.cwd);
          open(session.id);
        },
        "failed to create session",
        { cwd: request.cwd, field: true },
      );
    } else if (mode === "schedule") {
      const built = withScheduleTiming(form.scheduleRequest(schedule.prompt, presets.selectedId), schedule);
      if ("error" in built) {
        setFailure({ message: built.error, retry: false });
        return;
      }
      const payload = built.payload;
      void run(
        async () => {
          await createSchedule(host, token, payload);
          pushRecentCwd(host, targetId, payload.cwd);
          toast({ message: payload.cron ? "Recurring launch scheduled" : "Launch scheduled" });
          onClose();
        },
        "failed to schedule",
        { cwd: payload.cwd, field: true },
      );
    } else if (mode === "attach") {
      if (attachTargetError(attach.target)) return;
      const payload = attachRequest(attach.target, attach.agent, attach.title);
      void run(async () => {
        const session = await attachTmux(host, token, payload);
        open(session.id);
      }, "failed to attach");
    } else {
      const thread = resume.selected;
      void run(
        async () => {
          const session = await resume.resume();
          if (session) open(session.id);
        },
        "failed to resume the thread",
        thread ? { cwd: thread.cwd } : undefined,
      );
    }
  }

  const blocked =
    (mode === "resume" && !resume.selected) ||
    (mode === "attach" && attachTargetError(attach.target) !== null) ||
    (mode === "schedule" && schedule.timingMode === "repeat" && !schedule.recurrenceValid);
  const primaryLabel = mode === "new" && pointer ? "Launch session" : PRIMARY[mode];

  const surface = useRef<HTMLDivElement>(null);
  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    // A dialog opened over this one is portaled elsewhere but still bubbles here.
    if (!pointer || event.defaultPrevented || event.key !== "Enter" || !(event.metaKey || event.ctrlKey)) return;
    if (!surface.current?.contains(event.target as Node)) return;
    event.preventDefault();
    if (!blocked) submit();
  }

  const formMode = mode === "new" || mode === "schedule";
  const column = pointer && wide && formMode;
  const modes = (
    <SegmentedControl
      label="Launch mode"
      value={mode}
      onChange={changeMode}
      segments={MODES.map((value) => ({ value, label: MODE_LABELS[value][pointer ? 0 : 1] }))}
      block={!pointer}
      disabled={busy}
    />
  );

  const header = (
    <div className={styles.header}>
      <div className={styles.titleBlock}>
        <h2 className={styles.title}>{TITLES[mode]}</h2>
        <TargetChip disabled={busy} />
      </div>
      {pointer ? modes : null}
    </div>
  );

  const fields = (
    <LaunchFields
      form={form}
      catalog={catalog}
      agents={defaults.launchableBackends}
      host={host}
      token={token}
      launchTargetId={targetId || null}
      recentCwds={recents}
      cwdError={cwdError}
      busy={busy}
      presetRow={column ? null : <PresetRow controls={presets} catalog={catalog} />}
    >
      {mode === "schedule" ? <ScheduleWhen host={host} token={token} value={schedule} onChange={updateSchedule} /> : null}
    </LaunchFields>
  );

  const notice = failure ? (
    <div className={styles.notice} role="alert">
      <span className={styles.noticeGlyph}>
        <Icon name="alert" size={16} />
      </span>
      <p className={styles.noticeText}>{failure.message}</p>
      {failure.retry ? (
        <ActionChip icon="refresh" disabled={busy || blocked} onClick={submit}>
          Retry
        </ActionChip>
      ) : (
        <span />
      )}
      <IconButton label="Dismiss" size="sm" onClick={() => setFailure(null)}>
        <Icon name="close" size={16} />
      </IconButton>
    </div>
  ) : null;

  const footer = (
    <div className={styles.footer}>
      {notice}
      <div className={styles.actions}>
        {mode === "resume" && !pointer && resume.more > 0 ? (
          <ActionChip icon="chevron-down" onClick={resume.showMore}>
            {resume.more} more threads
          </ActionChip>
        ) : null}
        <span className={styles.grow} />
        {pointer ? (
          <Button variant="text" onClick={onClose}>
            Cancel
          </Button>
        ) : null}
        <Button
          variant="primary"
          block={!pointer && mode !== "resume"}
          busy={busy}
          disabled={blocked}
          onClick={submit}
        >
          {primaryLabel}
        </Button>
      </div>
    </div>
  );

  return (
    <>
      <Dialog open onClose={onClose} title={TITLES[mode]} header={header} footer={footer} size="xl" flush>
        <div ref={surface} className={cx(pointer && styles.pointerLayout, column && styles.withColumn)} onKeyDown={onKeyDown}>
          {column ? <PresetsColumn controls={presets} catalog={catalog} /> : null}
          <div className={styles.main}>
            {pointer ? null : <div className={styles.touchModes}>{modes}</div>}
            {formMode ? fields : null}
            {mode === "resume" ? (
              <ResumePane
                resume={resume}
                catalog={catalog}
                targetLabel={activeTarget?.name ?? null}
                busy={busy}
                onDeleteFailed={reportError}
              />
            ) : null}
            {mode === "attach" ? (
              <AttachPane value={attach} onChange={setAttach} agents={defaults.launchableBackends} catalog={catalog} busy={busy} />
            ) : null}
          </div>
        </div>
      </Dialog>
      <PresetDialogs
        controls={presets}
        spec={form.presetSpec()}
        catalog={catalog}
        profiles={form.caps.accountProfiles}
        usageProviders={launch.usageProviderOptions}
      />
      {sshRetry && activeTarget ? (
        <SshConnectDialog
          targetName={activeTarget.name}
          onConnect={async (password) => {
            const outcome = await launchTargets.connect(activeTarget.id, password);
            if (outcome.connected) queueMicrotask(sshRetry);
            return outcome;
          }}
          onClose={() => setSshRetry(null)}
        />
      ) : null}
    </>
  );
}

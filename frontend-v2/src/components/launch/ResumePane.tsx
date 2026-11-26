"use client";

import { useId, useState } from "react";

import type { BackendCatalog } from "@waypoint/web-core/backends";
import { humaniseBackend, transportPresentation } from "@waypoint/web-core/backends";
import { envSummary } from "@waypoint/web-core/launch/env";
import { deleteThreadMessage, formatThreadTime, threadEmptyHint, threadFilterOptions, type AgentThread } from "@waypoint/web-core/launch/threads";

import { Button } from "@/components/ui/Button";
import { ChoiceField } from "@/components/ui/Choice";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Sheet } from "@/components/ui/Dialog";
import { SearchInput } from "@/components/ui/Field";
import { Icon } from "@/components/ui/Icon";
import { MenuButton } from "@/components/ui/MenuButton";
import { SettingsRow, SwitchRow } from "@/components/ui/Settings";
import { cx } from "@/lib/cx";
import { usePointerUI } from "@/lib/layout";

import { EnvRows } from "./EnvRows";
import { threadKey, type ResumeState } from "./useResume";
import styles from "./ResumePane.module.css";

interface ResumePaneProps {
  resume: ResumeState;
  catalog: BackendCatalog;
  targetLabel: string | null;
  busy: boolean;
  onDeleteFailed: (message: string) => void;
}

// Pick a stored thread, then resume it with one primary. The interface, model and
// environment apply when one agent is chosen.
export function ResumePane({ resume, catalog, targetLabel, busy, onDeleteFailed }: ResumePaneProps) {
  const pointer = usePointerUI();
  const id = useId();
  const [envOpen, setEnvOpen] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const [deleting, setDeleting] = useState<AgentThread | null>(null);
  const label = (agent: string) => humaniseBackend(agent, catalog);
  const filters = threadFilterOptions(resume.backends);
  const agent = resume.agent;

  const env = <EnvRows entries={resume.envEntries} onChange={resume.setEnvEntries} disabled={busy} />;
  const settings = (
    <div className={pointer ? styles.grid : styles.rows}>
      {filters.length > 1 ? (
        <ChoiceField
          label="Agent"
          value={resume.filter}
          options={filters.map((filter) => ({
            value: filter,
            label: `${filter === "all" ? "All" : label(filter)}  ${resume.counts[filter] ?? 0} threads`,
          }))}
          onChange={resume.setFilter}
          disabled={busy}
        />
      ) : null}
      {agent && resume.transports.length > 1 ? (
        <ChoiceField
          label="Interface"
          value={resume.transport}
          options={resume.transports.map((value) => {
            const shown = transportPresentation(value, catalog);
            return { value, label: shown.name, detail: shown.description };
          })}
          onChange={resume.setTransport}
          disabled={busy}
        />
      ) : null}
      {agent && resume.showModel && resume.models.options.length > 0 ? (
        <ChoiceField
          label="Model"
          value={resume.models.selected ?? ""}
          options={resume.models.options
            .filter((model) => !model.hidden)
            .map((model) => ({ value: model.id, label: model.label }))}
          onChange={resume.models.setSelected}
          disabled={busy}
        />
      ) : null}
    </div>
  );

  return (
    <div className={styles.pane}>
      {settings}
      <div className={styles.rows}>
        <SwitchRow label="Replay history" checked={resume.importHistory} onChange={resume.setImportHistory} />
        {agent ? (
          pointer ? (
            <div className={styles.advanced}>
              <button type="button" className={styles.disclosure} aria-expanded={advanced} onClick={() => setAdvanced((value) => !value)}>
                <span className={styles.disclosureTitle}>Advanced</span>
                {advanced ? null : <span className={styles.muted}>{envSummary(resume.envEntries) || "No environment overrides"}</span>}
                <Icon name={advanced ? "chevron-up" : "chevron-down"} size={18} />
              </button>
              {advanced ? (
                <div className={styles.envField}>
                  <span className={styles.muted}>Environment variables</span>
                  {env}
                </div>
              ) : null}
            </div>
          ) : (
            <SettingsRow
              label="Environment"
              detail={envSummary(resume.envEntries) || "None"}
              mono={Boolean(envSummary(resume.envEntries))}
              onPress={() => setEnvOpen(true)} />
          )
        ) : null}
      </div>
      <SearchInput
        aria-label="Filter threads"
        placeholder="Filter threads"
        value={resume.query}
        onChange={(event) => resume.setQuery(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Escape" && resume.query) {
            event.preventDefault();
            event.nativeEvent.stopImmediatePropagation();
            resume.setQuery("");
          }
        }}
      />
      {resume.loading && resume.threads.length === 0 ? <p className={styles.note}>Loading threads…</p> : null}
      {!resume.loading && resume.threads.length === 0 ? (
        <p className={styles.note}>{resume.query.trim() ? "No threads match." : threadEmptyHint(resume.filter, catalog, targetLabel)}</p>
      ) : null}
      <ul className={styles.threads} role="radiogroup" aria-label="Threads" id={`${id}-threads`}>
        {resume.shown.map((thread) => {
          const selected = resume.selected !== null && threadKey(resume.selected) === threadKey(thread);
          return (
            <li key={threadKey(thread)} className={cx(styles.thread, selected && styles.selected)}>
              <button
                type="button"
                role="radio"
                aria-checked={selected}
                className={styles.threadButton}
                disabled={busy}
                onClick={() => resume.select(thread)}
              >
                <span className={cx(styles.radio, selected && styles.radioOn)} aria-hidden="true" />
                <span className={styles.threadBody}>
                  <span className={styles.threadHead}>
                    <span className={styles.threadTitle}>{thread.title}</span>
                    <time className={styles.time} dateTime={thread.updated_at}>
                      {formatThreadTime(thread.updated_at)}
                    </time>
                  </span>
                  {thread.preview ? <span className={styles.preview}>{thread.preview}</span> : null}
                  <span className={styles.meta}>
                    {resume.filter === "all" && resume.backends.length > 1 ? <span>{label(thread.backend)}</span> : null}
                    {thread.branch ? <span className={styles.branch}>{thread.branch}</span> : null}
                    {thread.repo_name ? <span>{thread.repo_name}</span> : null}
                  </span>
                </span>
              </button>
              {resume.canDelete(thread) ? (
                <span className={styles.menu}>
                  <MenuButton
                    label={`${thread.title} actions`}
                    title={thread.title}
                    size="sm"
                    items={[{ id: "delete", label: "Delete transcript", tone: "danger" }]}
                    onSelect={() => setDeleting(thread)}
                  />
                </span>
              ) : null}
            </li>
          );
        })}
      </ul>
      {pointer && resume.more > 0 ? (
        <Button variant="text" size="sm" onClick={resume.showMore}>
          {resume.more} more threads
        </Button>
      ) : null}
      {pointer ? null : (
        <Sheet
          open={envOpen}
          onClose={() => setEnvOpen(false)}
          title="Environment"
          footer={
            <Button variant="primary" block onClick={() => setEnvOpen(false)}>
              Done
            </Button>
          }
        >
          {env}
        </Sheet>
      )}
      <ConfirmDialog
        open={deleting !== null}
        title="Delete transcript"
        detail={deleting ? deleteThreadMessage(deleting) : ""}
        confirmLabel="Delete"
        onConfirm={async () => {
          if (!deleting) return;
          try {
            await resume.remove(deleting);
          } catch (error) {
            onDeleteFailed(error instanceof Error ? error.message : "failed to delete the transcript");
          }
        }}
        onClose={() => setDeleting(null)}
      />
    </div>
  );
}

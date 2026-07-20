"use client";

import {
  useMemo,
  useState,
  type KeyboardEvent,
  type RefObject,
} from "react";

import {
  CHANNEL_GROUP_LABELS,
  CHANNEL_GROUP_ORDER,
  classifyChannel,
  type ChannelGroupKey,
} from "@/lib/board";
import { BoardChannel, ManagerSummary } from "@/lib/types";

export type PostMode = "ticket" | "update";
export type UpdateWriteMode = "append" | "cell";

export interface TicketSubmit {
  title: string;
  details: string;
  priority: string;
}

export interface UpdateSubmit {
  channel: string;
  text: string;
  key: string | null;
}

export type SubmitResult = { ok: boolean; error?: string };

interface BoardPostSheetProps {
  initialMode: PostMode;
  // Focus lands here on open (routed through Sheet's initialFocusRef, which
  // wins the open-focus race against a child autoFocus).
  initialFocusRef: RefObject<HTMLElement | null>;
  // Whether Ticket mode is offered at all. False → no manager, Update only.
  ticketAvailable: boolean;
  // Ticket mode can post: the selected manager's state is loaded for the
  // current selection and carries an intake channel.
  managerReady: boolean;
  // Loaded but misconfigured (no intake channel) vs. simply still loading.
  managerMisconfigured: boolean;
  project: string | null;
  ticketsChannel: string | null;
  priorityLevels: string[];
  managers: ManagerSummary[];
  selectedManagerId: string | null;
  onSelectManager: (id: string) => void;
  channels: BoardChannel[];
  // The Update target the current view implies.
  defaultUpdateChannel: string | null;
  posting: boolean;
  onSubmitTicket: (draft: TicketSubmit) => Promise<SubmitResult>;
  onSubmitUpdate: (draft: UpdateSubmit) => Promise<SubmitResult>;
}

const DEFAULT_PRIORITY = "p2";

function pickDefaultPriority(levels: string[]): string {
  if (levels.includes(DEFAULT_PRIORITY)) return DEFAULT_PRIORITY;
  return levels[0] ?? DEFAULT_PRIORITY;
}

// A modified-Enter (⌘/Ctrl+Enter) submit, the repo-wide convention for
// multi-line composers.
function isSubmitChord(event: KeyboardEvent): boolean {
  return event.key === "Enter" && (event.metaKey || event.ctrlKey);
}

export function BoardPostSheet({
  initialMode,
  initialFocusRef,
  ticketAvailable,
  managerReady,
  managerMisconfigured,
  project,
  ticketsChannel,
  priorityLevels,
  managers,
  selectedManagerId,
  onSelectManager,
  channels,
  defaultUpdateChannel,
  posting,
  onSubmitTicket,
  onSubmitUpdate,
}: BoardPostSheetProps) {
  // The sheet owns its two drafts so toggling modes never leaks a field or
  // target across; closing the sheet unmounts this component and resets them.
  const [mode, setMode] = useState<PostMode>(
    ticketAvailable ? initialMode : "update",
  );

  // Ticket draft.
  const [title, setTitle] = useState("");
  const [details, setDetails] = useState("");
  const [priority, setPriority] = useState(() =>
    pickDefaultPriority(priorityLevels),
  );

  // Update draft. `useNamed` reveals a typed channel field; when the board has
  // no channels to pick, start there so a first post is possible.
  const [useNamed, setUseNamed] = useState(channels.length === 0);
  const [existingChannel, setExistingChannel] = useState(
    defaultUpdateChannel ?? channels[0]?.channel ?? "",
  );
  const [namedChannel, setNamedChannel] = useState("");
  const [writeMode, setWriteMode] = useState<UpdateWriteMode>("append");
  const [updateKey, setUpdateKey] = useState("");
  const [updateText, setUpdateText] = useState("");
  const [pickerSearch, setPickerSearch] = useState("");

  const [submitError, setSubmitError] = useState<string | null>(null);

  const priorityDefault = useMemo(
    () => pickDefaultPriority(priorityLevels),
    [priorityLevels],
  );
  // Keep the chosen priority valid as the manager (and its levels) load in.
  const effectivePriority = priorityLevels.includes(priority)
    ? priority
    : priorityDefault;

  const targetChannel = (
    useNamed ? namedChannel : existingChannel
  ).trim();
  const targetsIntake =
    !!ticketsChannel && targetChannel === ticketsChannel;
  const intakeWarning =
    targetsIntake && writeMode === "append" && targetChannel.length > 0;

  const groupedChannels = useMemo(() => {
    const filter = pickerSearch.trim().toLowerCase();
    const groups: Record<ChannelGroupKey, BoardChannel[]> = {
      manager: [],
      ticket: [],
      job: [],
      other: [],
    };
    for (const channel of channels) {
      if (filter && !channel.channel.toLowerCase().includes(filter)) continue;
      groups[classifyChannel(channel.channel)].push(channel);
    }
    return groups;
  }, [channels, pickerSearch]);

  const ticketValid = title.trim().length > 0 && managerReady;
  const updateValid =
    targetChannel.length > 0 &&
    updateText.trim().length > 0 &&
    (writeMode === "append" || updateKey.trim().length > 0);

  async function submitTicket() {
    if (posting || !ticketValid) return;
    setSubmitError(null);
    const res = await onSubmitTicket({
      title: title.trim(),
      details: details,
      priority: effectivePriority,
    });
    // On success the parent closes the sheet (unmounting us); only a failure
    // returns here with the draft still mounted.
    if (!res.ok && res.error) setSubmitError(res.error);
  }

  async function submitUpdate() {
    if (posting || !updateValid) return;
    setSubmitError(null);
    const res = await onSubmitUpdate({
      channel: targetChannel,
      text: updateText.trim(),
      key: writeMode === "cell" ? updateKey.trim() : null,
    });
    if (!res.ok && res.error) setSubmitError(res.error);
  }

  return (
    <div className="board-post">
      {ticketAvailable ? (
        <div
          className="segmented board-post-modes"
          role="group"
          aria-label="Posting mode"
        >
          <button
            type="button"
            className={`segmented-item${mode === "ticket" ? " active" : ""}`}
            aria-pressed={mode === "ticket"}
            onClick={() => {
              setMode("ticket");
              setSubmitError(null);
            }}
          >
            Ticket
          </button>
          <button
            type="button"
            className={`segmented-item${mode === "update" ? " active" : ""}`}
            aria-pressed={mode === "update"}
            onClick={() => {
              setMode("update");
              setSubmitError(null);
            }}
          >
            Update
          </button>
        </div>
      ) : null}

      {mode === "ticket" ? (
        <div className="board-post-form">
          <div className="board-post-target" aria-label="Ticket destination">
            <span className="board-post-label">Destination</span>
            {managers.length > 1 ? (
              <span className="board-post-select">
                <select
                  value={selectedManagerId ?? ""}
                  onChange={(event) => onSelectManager(event.target.value)}
                  aria-label="Project"
                >
                  {managers.map((manager) => (
                    <option key={manager.id} value={manager.id}>
                      {manager.project} · manager intake
                    </option>
                  ))}
                </select>
              </span>
            ) : (
              <span className="board-post-target-name">
                {project ?? "manager"}
                {managerReady ? (
                  <span className="board-post-target-sub"> · manager intake</span>
                ) : !managerMisconfigured ? (
                  <span className="board-post-target-sub"> · loading…</span>
                ) : null}
              </span>
            )}
          </div>

          <label className="board-post-field">
            <span className="board-post-label">Title</span>
            <input
              className="board-post-input"
              ref={
                initialMode === "ticket"
                  ? (initialFocusRef as RefObject<HTMLInputElement | null>)
                  : undefined
              }
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              onKeyDown={(event) => {
                if (isSubmitChord(event)) {
                  event.preventDefault();
                  void submitTicket();
                }
              }}
              placeholder="What needs to happen"
              aria-label="Ticket title"
            />
          </label>

          <label className="board-post-field">
            <span className="board-post-label">
              Details <span className="board-post-optional">optional</span>
            </span>
            <textarea
              className="board-post-textarea"
              value={details}
              onChange={(event) => setDetails(event.target.value)}
              onKeyDown={(event) => {
                if (isSubmitChord(event)) {
                  event.preventDefault();
                  void submitTicket();
                }
              }}
              rows={5}
              placeholder="Context, acceptance criteria, links…"
              aria-label="Ticket details"
            />
          </label>

          <label className="board-post-field board-post-field-inline">
            <span className="board-post-label">Priority</span>
            <span className="board-post-select">
              <select
                value={effectivePriority}
                onChange={(event) => setPriority(event.target.value)}
                aria-label="Priority"
                disabled={priorityLevels.length === 0}
              >
                {(priorityLevels.length > 0
                  ? priorityLevels
                  : [DEFAULT_PRIORITY]
                ).map((level) => (
                  <option key={level} value={level}>
                    {level}
                  </option>
                ))}
              </select>
            </span>
          </label>

          {managerMisconfigured ? (
            <p className="board-post-note board-post-note-warn" role="alert">
              This project has no configured intake channel. It can’t receive a
              ticket until that’s set.
            </p>
          ) : (
            <p className="board-post-note">
              The manager registers this as a ticket shortly after you submit.
            </p>
          )}

          {submitError ? (
            <p className="board-post-error" role="alert">
              {submitError}
            </p>
          ) : null}

          <div className="board-post-foot">
            <span className="board-post-hint" aria-hidden="true">
              ⌘↵ submit
            </span>
            <button
              type="button"
              className="primary board-post-submit"
              onClick={() => void submitTicket()}
              disabled={posting || !ticketValid}
            >
              {posting ? "Submitting…" : "Submit ticket"}
            </button>
          </div>
        </div>
      ) : (
        <div className="board-post-form">
          <div className="board-post-target-picker">
            <span className="board-post-label">Channel</span>
            {useNamed ? (
              <div className="board-post-named">
                <input
                  className="board-post-input"
                  ref={
                    initialMode === "update" && channels.length === 0
                      ? (initialFocusRef as RefObject<HTMLInputElement | null>)
                      : undefined
                  }
                  value={namedChannel}
                  onChange={(event) => setNamedChannel(event.target.value)}
                  placeholder="channel — e.g. topic:notes"
                  aria-label="New or named channel"
                />
                {channels.length > 0 ? (
                  <button
                    type="button"
                    className="board-post-linkbtn"
                    onClick={() => setUseNamed(false)}
                  >
                    ← Pick an existing channel
                  </button>
                ) : null}
              </div>
            ) : (
              <div className="board-post-picker">
                <input
                  className="board-post-input board-post-picker-search"
                  type="search"
                  value={pickerSearch}
                  onChange={(event) => setPickerSearch(event.target.value)}
                  placeholder="Filter channels"
                  aria-label="Filter channels"
                />
                <div className="board-post-picker-list" role="listbox">
                  {CHANNEL_GROUP_ORDER.map((group) => {
                    const rows = groupedChannels[group];
                    if (rows.length === 0) return null;
                    return (
                      <div key={group} className="board-post-picker-group">
                        <span className="board-post-picker-grouplabel">
                          {CHANNEL_GROUP_LABELS[group]}
                        </span>
                        {rows.map((row) => (
                          <button
                            key={row.channel}
                            type="button"
                            role="option"
                            aria-selected={row.channel === existingChannel}
                            className={`board-post-picker-item${
                              row.channel === existingChannel ? " is-active" : ""
                            }`}
                            onClick={() => setExistingChannel(row.channel)}
                          >
                            <span className="board-post-picker-name">
                              {row.channel}
                            </span>
                            <span className="board-post-picker-count">
                              {row.entry_count}
                            </span>
                          </button>
                        ))}
                      </div>
                    );
                  })}
                </div>
                <button
                  type="button"
                  className="board-post-linkbtn"
                  onClick={() => {
                    setUseNamed(true);
                    setNamedChannel("");
                  }}
                >
                  + New or named channel
                </button>
              </div>
            )}
          </div>

          <div
            className="segmented segmented-quiet board-post-writemode"
            role="group"
            aria-label="Write mode"
          >
            <button
              type="button"
              className={`segmented-item${
                writeMode === "append" ? " active" : ""
              }`}
              aria-pressed={writeMode === "append"}
              onClick={() => setWriteMode("append")}
              title="Appends a new post to the channel log"
            >
              Append
            </button>
            <button
              type="button"
              className={`segmented-item${writeMode === "cell" ? " active" : ""}`}
              aria-pressed={writeMode === "cell"}
              onClick={() => setWriteMode("cell")}
              title="Sets a keyed cell — the latest value for that key wins"
            >
              Set cell
            </button>
          </div>

          {intakeWarning ? (
            <p className="board-post-note board-post-note-warn" role="alert">
              A keyless post here is treated as manager ticket intake.{" "}
              {ticketAvailable ? (
                <button
                  type="button"
                  className="board-post-linkbtn board-post-inlineswitch"
                  onClick={() => {
                    setMode("ticket");
                    setSubmitError(null);
                  }}
                >
                  Switch to Ticket
                </button>
              ) : null}{" "}
              or choose Set cell / another channel.
            </p>
          ) : null}

          {writeMode === "cell" ? (
            <label className="board-post-field">
              <span className="board-post-label">Key</span>
              <input
                className="board-post-input"
                value={updateKey}
                onChange={(event) => setUpdateKey(event.target.value)}
                placeholder="key — e.g. status"
                aria-label="Cell key"
              />
            </label>
          ) : null}

          <label className="board-post-field">
            <span className="board-post-label">Message</span>
            <textarea
              className="board-post-textarea"
              ref={
                initialMode === "update" && channels.length > 0
                  ? (initialFocusRef as RefObject<HTMLTextAreaElement | null>)
                  : undefined
              }
              value={updateText}
              onChange={(event) => setUpdateText(event.target.value)}
              onKeyDown={(event) => {
                if (isSubmitChord(event)) {
                  event.preventDefault();
                  void submitUpdate();
                }
              }}
              rows={4}
              placeholder="Message"
              aria-label="Message"
            />
          </label>

          {submitError ? (
            <p className="board-post-error" role="alert">
              {submitError}
            </p>
          ) : null}

          <div className="board-post-foot">
            <span className="board-post-hint" aria-hidden="true">
              ⌘↵ submit
            </span>
            <button
              type="button"
              className="primary board-post-submit"
              onClick={() => void submitUpdate()}
              disabled={posting || !updateValid}
            >
              {posting
                ? writeMode === "cell"
                  ? "Setting…"
                  : "Posting…"
                : writeMode === "cell"
                  ? "Set cell"
                  : "Post update"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

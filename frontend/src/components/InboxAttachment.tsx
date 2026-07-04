"use client";

import { useState } from "react";

import { attachmentUrl } from "@/lib/api";
import type { InboxAttachmentRef } from "@/lib/types";

// Renders an inbox attachment ref. The backend denormalizes filename/kind onto
// the ref at post/submit time, so the name renders inline with no per-session
// lookup (mirrors how a chat message carries its attachment specs). Falls back
// to the id + an optimistic thumbnail for an unresolved ref (e.g. legacy rows
// or a since-deleted attachment).
export function InboxAttachment({
  host,
  token,
  attachmentRef,
}: {
  host: string;
  token: string;
  attachmentRef: InboxAttachmentRef;
}) {
  const [imageBroken, setImageBroken] = useState(false);
  const url = attachmentUrl(
    host,
    token,
    attachmentRef.session_id,
    attachmentRef.attachment_id,
  );
  const name = attachmentRef.filename ?? attachmentRef.attachment_id;
  // Glyph for a known non-image, or when an optimistic image fails to load.
  const showGlyph =
    imageBroken ||
    (attachmentRef.kind != null && attachmentRef.kind !== "image");

  return (
    <a
      href={url}
      target="_blank"
      rel="noreferrer"
      className="message-attachment-file"
      title={name}
    >
      {showGlyph ? (
        <span className="attachment-tile" aria-hidden="true">
          <FileGlyph />
        </span>
      ) : (
        // Token-query serve URL — next/image can't optimize it.
        // eslint-disable-next-line @next/next/no-img-element
        <img
          className="message-attachment-thumb"
          src={url}
          alt={name}
          loading="lazy"
          onError={() => setImageBroken(true)}
        />
      )}
      <span className="attachment-body">
        <span className="attachment-name">{name}</span>
      </span>
    </a>
  );
}

export function InboxAttachments({
  host,
  token,
  refs,
}: {
  host: string;
  token: string;
  refs: InboxAttachmentRef[];
}) {
  if (refs.length === 0) return null;
  return (
    <div className="message-attachments">
      {refs.map((ref) => (
        <InboxAttachment
          key={`${ref.session_id}:${ref.attachment_id}`}
          host={host}
          token={token}
          attachmentRef={ref}
        />
      ))}
    </div>
  );
}

function FileGlyph() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="15"
      height="15"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M13 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V9z" />
      <path d="M13 3v6h6" />
    </svg>
  );
}

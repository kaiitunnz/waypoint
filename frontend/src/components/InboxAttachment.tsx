"use client";

import { useState } from "react";

import { attachmentUrl } from "@/lib/api";
import type { InboxAttachmentRef } from "@/lib/types";

// Renders an inbox attachment ref (a {session_id, attachment_id} pointer into
// the requesting session's store). Mirrors AttachmentTray's MessageAttachmentCard
// markup, but sourced from a bare ref rather than an EventRecord: no spec is
// available, so it optimistically renders a thumbnail and falls back to a file
// glyph link when the blob is not an image (or is gone).
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
  const name = attachmentRef.attachment_id;

  return (
    <a
      href={url}
      target="_blank"
      rel="noreferrer"
      className="message-attachment-file"
      title={name}
    >
      {imageBroken ? (
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

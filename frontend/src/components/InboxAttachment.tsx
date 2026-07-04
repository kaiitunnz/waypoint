"use client";

import { useEffect, useState } from "react";

import { attachmentUrl, fetchSessionAttachments } from "@/lib/api";
import type { InboxAttachmentRef, SessionAttachment } from "@/lib/types";

// One fetch per (host, session) shared across every attachment instance: an
// inbox ref is only {session_id, attachment_id}, so the filename/kind are
// resolved from the session's attachment specs. The cached promise resolves to
// an empty map on error (e.g. the requester session is gone) so consumers fall
// back to the id without an unhandled rejection.
const specCache = new Map<string, Promise<Map<string, SessionAttachment>>>();

function loadSpecs(
  host: string,
  token: string,
  sessionId: string,
): Promise<Map<string, SessionAttachment>> {
  const key = `${host}::${sessionId}`;
  let entry = specCache.get(key);
  if (!entry) {
    entry = fetchSessionAttachments(host, token, sessionId)
      .then((list) => new Map(list.map((spec) => [spec.id, spec])))
      .catch(() => new Map<string, SessionAttachment>());
    specCache.set(key, entry);
  }
  return entry;
}

function useAttachmentSpec(
  host: string,
  token: string,
  ref: InboxAttachmentRef,
): SessionAttachment | undefined {
  const [spec, setSpec] = useState<SessionAttachment | undefined>(undefined);
  useEffect(() => {
    let active = true;
    loadSpecs(host, token, ref.session_id).then((specs) => {
      if (active) setSpec(specs.get(ref.attachment_id));
    });
    return () => {
      active = false;
    };
  }, [host, token, ref.session_id, ref.attachment_id]);
  return spec;
}

// Renders an inbox attachment ref, resolving the uploaded filename and kind
// from the session's attachment specs (falling back to the id + an optimistic
// thumbnail while unresolved). Mirrors AttachmentTray's MessageAttachmentCard.
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
  const spec = useAttachmentSpec(host, token, attachmentRef);
  const url = attachmentUrl(
    host,
    token,
    attachmentRef.session_id,
    attachmentRef.attachment_id,
  );
  const name = spec?.filename ?? attachmentRef.attachment_id;
  // Glyph for a known non-image, or when an optimistic image fails to load.
  const showGlyph = imageBroken || (spec != null && spec.kind !== "image");

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

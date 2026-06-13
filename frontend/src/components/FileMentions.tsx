"use client";

import { forwardRef, MutableRefObject } from "react";

import { FileIcon, formatBytes, typeTag } from "@/components/AttachmentTray";
import { attachmentUrl } from "@/lib/api";
import type { SessionAttachment } from "@/lib/types";

interface FileMentionsProps {
  host: string;
  token: string;
  sessionId: string;
  mentions: ReadonlyArray<SessionAttachment>;
  activeIndex: number;
  itemRefs: MutableRefObject<Array<HTMLButtonElement | null>>;
  onApply: (index: number) => void;
  onHover: (index: number) => void;
}

// The `@`-mention popover: session files matching the typed query, each shown
// with a thumbnail/glyph, name, and size. Reuses the slash-suggestion styling.
export const FileMentions = forwardRef<HTMLUListElement, FileMentionsProps>(
  function FileMentions(
    { host, token, sessionId, mentions, activeIndex, itemRefs, onApply, onHover },
    ref,
  ) {
    return (
      <ul className="slash-suggestions file-mentions" role="listbox" ref={ref}>
        {mentions.map((file, index) => {
          const url = attachmentUrl(host, token, sessionId, file.id);
          return (
            <li key={file.id}>
              <button
                ref={(node) => {
                  itemRefs.current[index] = node;
                }}
                type="button"
                role="option"
                aria-selected={index === activeIndex}
                className={`slash-suggestion file-mention ${index === activeIndex ? "active" : ""}`}
                onMouseDown={(event) => {
                  event.preventDefault();
                  onApply(index);
                }}
                onMouseEnter={() => onHover(index)}
              >
                {file.kind === "image" ? (
                  // Token-query serve URL — next/image can't optimize it.
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    className="file-mention-thumb"
                    src={url}
                    alt={file.filename}
                    loading="lazy"
                  />
                ) : (
                  <span className="attachment-tile" aria-hidden="true">
                    <FileIcon />
                  </span>
                )}
                <span className="file-mention-body">
                  <span className="slash-name">{file.filename}</span>
                  <span className="slash-desc">
                    {typeTag(file.filename, file.kind === "image")} ·{" "}
                    {formatBytes(file.size)}
                  </span>
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    );
  },
);

"use client";

import { type CSSProperties, Fragment, useEffect, useMemo, useState } from "react";

import { formatBytes } from "@/components/AttachmentTray";
import { MarkdownMessage } from "@/components/MarkdownMessage";
import type { WorkspaceFile } from "@/lib/api";
import { highlightToLines, type HighlightToken } from "@/lib/highlight";

const IMAGE_EXTS = new Set([
  ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
  ".ico", ".bmp", ".avif",
]);

function isImagePath(path: string): boolean {
  const dot = path.lastIndexOf(".");
  if (dot < 0) return false;
  return IMAGE_EXTS.has(path.slice(dot).toLowerCase());
}

function isMarkdownPath(path: string): boolean {
  const lower = path.toLowerCase();
  return lower.endsWith(".md") || lower.endsWith(".markdown");
}

interface FilePreviewProps {
  file: WorkspaceFile;
  rawUrl: string;
}

export function FilePreview({ file, rawUrl }: FilePreviewProps) {
  const [softWrap, setSoftWrap] = useState(false);
  const [mdView, setMdView] = useState<"rendered" | "source">("rendered");
  const [imageError, setImageError] = useState(false);

  if (file.binary || file.content === null) {
    return (
      <div className="wp-file-binary">
        <span className="wp-file-binary-icon" aria-hidden="true">⬜</span>
        <p className="wp-file-binary-label">
          {file.binary ? "Binary file" : "File too large to preview"}
        </p>
        <p className="wp-file-binary-size">{formatBytes(file.size)}</p>
        <a
          href={rawUrl}
          target="_blank"
          rel="noreferrer"
          className="secondary wp-file-binary-open"
        >
          Open raw ↗
        </a>
      </div>
    );
  }

  if (isImagePath(file.path) && !imageError) {
    return (
      <div className="wp-file-image">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={rawUrl}
          alt={file.path}
          className="wp-file-img"
          onError={() => setImageError(true)}
        />
      </div>
    );
  }

  if (isImagePath(file.path)) {
    return (
      <div className="wp-file-binary">
        <span className="wp-file-binary-icon" aria-hidden="true">⬜</span>
        <p className="wp-file-binary-label">Could not load image</p>
        <p className="wp-file-binary-size">{formatBytes(file.size)}</p>
        <a
          href={rawUrl}
          target="_blank"
          rel="noreferrer"
          className="secondary wp-file-binary-open"
        >
          Open raw ↗
        </a>
      </div>
    );
  }

  if (isMarkdownPath(file.path) && file.content) {
    return (
      <div className="wp-file-markdown">
        <div className="wp-md-toggle">
          <button
            type="button"
            className={`wp-md-btn${mdView === "rendered" ? " active" : ""}`}
            onClick={() => setMdView("rendered")}
          >
            Rendered
          </button>
          <button
            type="button"
            className={`wp-md-btn${mdView === "source" ? " active" : ""}`}
            onClick={() => setMdView("source")}
          >
            Source
          </button>
        </div>
        {mdView === "rendered" ? (
          <div className="wp-md-rendered">
            <MarkdownMessage text={file.content} />
          </div>
        ) : (
          <FileCodeView
            content={file.content}
            path={file.path}
            softWrap={softWrap}
            setSoftWrap={setSoftWrap}
          />
        )}
      </div>
    );
  }

  return (
    <FileCodeView
      content={file.content ?? ""}
      path={file.path}
      softWrap={softWrap}
      setSoftWrap={setSoftWrap}
    />
  );
}

function FileCodeView({
  content,
  path,
  softWrap,
  setSoftWrap,
}: {
  content: string;
  path: string;
  softWrap: boolean;
  setSoftWrap: (v: boolean) => void;
}) {
  // Render raw lines synchronously, then upgrade to highlighted token lines once
  // the lazy highlighter resolves. Each "line" is an array of tokens; a raw line
  // is a single classless token (empty array for a blank line).
  const plain = useMemo<HighlightToken[][]>(
    () => content.split("\n").map((line) => (line ? [{ text: line, className: "" }] : [])),
    [content],
  );
  const [lines, setLines] = useState<HighlightToken[][]>(plain);

  useEffect(() => {
    setLines(plain);
    let cancelled = false;
    void highlightToLines(content, path).then((highlighted) => {
      if (!cancelled && highlighted) setLines(highlighted);
    });
    return () => {
      cancelled = true;
    };
  }, [content, path, plain]);

  const lnw = `${String(lines.length).length}ch`;
  return (
    <div className="wp-code-wrap">
      <div className="wp-code-toolbar">
        <span className="wp-code-lang">{path.split("/").pop()}</span>
        <button
          type="button"
          className={`wp-wrap-toggle${softWrap ? " active" : ""}`}
          onClick={() => setSoftWrap(!softWrap)}
          title={softWrap ? "Disable soft wrap" : "Enable soft wrap"}
        >
          wrap
        </button>
      </div>
      <pre
        className={`diff-code wp-code-pre wp-hl${softWrap ? " soft-wrap" : ""}`}
        style={{ "--lnw": lnw } as CSSProperties}
        aria-label={`Contents of ${path}`}
      >
        {lines.map((tokens, idx) => (
          <span key={idx} className="wp-code-line">
            <span className="wp-line-num">{idx + 1}</span>
            <span className="wp-line-text">
              {tokens.length === 0
                ? " "
                : tokens.map((token, i) =>
                    token.className ? (
                      <span key={i} className={token.className}>
                        {token.text}
                      </span>
                    ) : (
                      <Fragment key={i}>{token.text}</Fragment>
                    ),
                  )}
            </span>
          </span>
        ))}
      </pre>
    </div>
  );
}

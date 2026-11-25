"use client";

import "katex/dist/katex.min.css";

import { memo, useEffect, useMemo, useState, type ComponentPropsWithoutRef, type CSSProperties, type ReactNode } from "react";
import Link from "next/link";
import ReactMarkdown, { type Components } from "react-markdown";

import { highlightFenceToLines, type HighlightToken } from "@waypoint/web-core/highlight";
import {
  COMMON_REHYPE_PLUGINS,
  COMMON_REMARK_PLUGINS,
  orderedListMarkerDigits,
  rehypeAppendReserve,
} from "@waypoint/web-core/markdown";
import { fenceLanguage, HIGHLIGHT_DEBOUNCE_MS, nodeToText } from "@waypoint/web-core/markdownCode";
import { sessionFilesHref } from "@waypoint/web-core/shellNav";
import { isWorkspacePathHref, remarkLinkifyPaths } from "@waypoint/web-core/workspacePaths";

import { cx } from "@/lib/cx";

import { useSessionContext } from "../SessionContext";
import { CopyButton } from "./CopyButton";
import highlight from "./Highlight.module.css";
import styles from "./Markdown.module.css";
import { Tokens } from "./Tokens";

// Hoisted so the plugin arrays and component overrides keep one identity across
// renders; with memo(), unchanged text skips the parse while a reply streams.
// remarkLinkifyPaths runs after remarkMath so a path-like TeX fragment is math.
const REMARK_PLUGINS = [...COMMON_REMARK_PLUGINS, remarkLinkifyPaths];
const REHYPE_PLUGINS = COMMON_REHYPE_PLUGINS;
const REHYPE_PLUGINS_RESERVED = [...COMMON_REHYPE_PLUGINS, rehypeAppendReserve];

function CodeBlock({ children }: { children?: ReactNode }) {
  const code = useMemo(() => nodeToText(children).replace(/\n$/, ""), [children]);
  const lang = useMemo(() => fenceLanguage(children), [children]);
  // Tagged with the text it was made from, so a growing block shows raw text, not a
  // shorter highlighted prefix.
  const [done, setDone] = useState<{ key: string; lines: HighlightToken[][] } | null>(null);
  const key = `${lang}\n${code}`;
  const lines = done?.key === key ? done.lines : null;

  useEffect(() => {
    if (!lang) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void highlightFenceToLines(code, lang).then((highlighted) => {
        if (!cancelled && highlighted) setDone({ key, lines: highlighted });
      });
    }, HIGHLIGHT_DEBOUNCE_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [code, lang, key]);

  return (
    <div className={styles.codeblock}>
      <CopyButton text={code} className={styles.codeCopy} label="Copy code" />
      <pre className={cx(styles.pre, lines && highlight.hl)}>
        {lines
          ? lines.map((tokens, index) => (
              <span key={index}>
                {index > 0 ? "\n" : null}
                <Tokens tokens={tokens} />
              </span>
            ))
          : children}
      </pre>
    </div>
  );
}

function MarkdownAnchor({ href, children, ...props }: ComponentPropsWithoutRef<"a">) {
  const { sessionId } = useSessionContext();
  if (href && isWorkspacePathHref(href)) {
    return (
      <Link href={sessionFilesHref(sessionId, href)} className={styles.path}>
        {children}
      </Link>
    );
  }
  return (
    <a href={href} rel="noreferrer" target="_blank" {...props}>
      {children}
    </a>
  );
}

const COMPONENTS: Components = {
  a: MarkdownAnchor,
  pre({ children }) {
    return <CodeBlock>{children}</CodeBlock>;
  },
  table({ children }) {
    return (
      <div className={styles.table}>
        <table>{children}</table>
      </div>
    );
  },
  ol({ node, start, style, children, ...props }) {
    const items = node?.children.filter((child) => child.type === "element" && child.tagName === "li").length ?? 1;
    const markerStyle = { ...style, "--ol-marker-digits": orderedListMarkerDigits(start, items) } as CSSProperties;
    return (
      <ol start={start} style={markerStyle} {...props}>
        {children}
      </ol>
    );
  },
};

interface MarkdownProps {
  text: string;
  // Leave room on the last line for a bubble's time and copy button.
  reserve?: boolean;
}

export const Markdown = memo(function Markdown({ text, reserve }: MarkdownProps) {
  return (
    <div className={styles.md}>
      <ReactMarkdown
        remarkPlugins={REMARK_PLUGINS}
        rehypePlugins={reserve ? REHYPE_PLUGINS_RESERVED : REHYPE_PLUGINS}
        components={COMPONENTS}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
});

import {
  Fragment,
  isValidElement,
  memo,
  useEffect,
  useMemo,
  useState,
  type ComponentPropsWithoutRef,
  type ReactNode,
} from "react";

import ReactMarkdown, { type Components } from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";

import { CopyCodeButton } from "@/components/CopyCodeButton";
import { useWorkspaceFileLink } from "@/components/WorkspaceFileLinkContext";
import { highlightFenceToLines, type HighlightToken } from "@/lib/highlight";
import { isWorkspacePathHref, remarkLinkifyPaths } from "@/lib/workspacePaths";

interface MarkdownMessageProps {
  text: string;
}

// Hoisted to module scope so the plugin array and component overrides keep a
// stable identity across renders — combined with the memo() below, an
// unchanged `text` skips the remark parse entirely during streaming.
const REMARK_PLUGINS = [remarkGfm, remarkBreaks, remarkLinkifyPaths];

// react-markdown renders fenced blocks as <pre><code>…</code></pre>; the pre
// override only receives the rendered tree, so recover the raw code by walking
// the children for the copy button.
function nodeToText(node: ReactNode): string {
  if (typeof node === "string") return node;
  if (typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(nodeToText).join("");
  if (isValidElement(node)) {
    return nodeToText((node.props as { children?: ReactNode }).children);
  }
  return "";
}

// react-markdown stamps the fence info onto the inner <code> as
// `language-<info>`; pull it back out for the highlighter.
function fenceLanguage(children: ReactNode): string {
  const child = Array.isArray(children) ? children[0] : children;
  if (isValidElement(child)) {
    const className = (child.props as { className?: string }).className ?? "";
    const match = /(?:^|\s)language-(\S+)/.exec(className);
    if (match) return match[1];
  }
  return "";
}

// Highlight only once the code stops changing: every streamed delta grows the
// text and resets this timer, so an in-flight message renders raw and a
// finalized one upgrades ~one debounce later. Comfortably longer than the gap
// between streamed tokens, short enough to feel instant on loaded history.
const HIGHLIGHT_DEBOUNCE_MS = 300;

function CodeBlock({ children }: { children?: ReactNode }) {
  const code = useMemo(() => nodeToText(children).replace(/\n$/, ""), [children]);
  const lang = useMemo(() => fenceLanguage(children), [children]);
  const [lines, setLines] = useState<HighlightToken[][] | null>(null);

  useEffect(() => {
    // Drop any stale highlight first so the raw text — not a shorter,
    // previously-tokenized prefix — shows while the block is still growing.
    setLines(null);
    if (!lang) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void highlightFenceToLines(code, lang).then((highlighted) => {
        if (!cancelled && highlighted) setLines(highlighted);
      });
    }, HIGHLIGHT_DEBOUNCE_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [code, lang]);

  return (
    <div className="markdown-codeblock">
      <CopyCodeButton text={code} />
      {lines ? (
        <pre className="markdown-pre wp-hl">
          {lines.map((tokens, idx) => (
            <Fragment key={idx}>
              {idx > 0 ? "\n" : null}
              {tokens.map((token, i) =>
                token.className ? (
                  <span key={i} className={token.className}>
                    {token.text}
                  </span>
                ) : (
                  <Fragment key={i}>{token.text}</Fragment>
                ),
              )}
            </Fragment>
          ))}
        </pre>
      ) : (
        <pre className="markdown-pre">{children}</pre>
      )}
    </div>
  );
}

function MarkdownAnchor({
  href,
  children,
  ...props
}: ComponentPropsWithoutRef<"a">) {
  const link = useWorkspaceFileLink();
  const fromBareText = props["data-wp-bare" as keyof typeof props] === "true";
  if (link && isWorkspacePathHref(href)) {
    return (
      <a
        href={href}
        onClick={(e) => {
          // Leave modified clicks (new tab/window) and non-primary buttons to
          // the browser; intercept only a plain left click.
          if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) {
            return;
          }
          e.preventDefault();
          link.openWorkspacePath(href as string, { fromBareText });
        }}
        {...props}
      >
        {children}
      </a>
    );
  }
  // A path we synthesized from prose but have nowhere to open (no session
  // context): render plain text rather than a dead navigation.
  if (fromBareText && isWorkspacePathHref(href)) {
    return <span>{children}</span>;
  }
  return (
    <a href={href} rel="noreferrer" target="_blank" {...props}>
      {children}
    </a>
  );
}

const COMPONENTS: Components = {
  a: MarkdownAnchor,
  code({
    inline,
    className,
    children,
    ...props
  }: ComponentPropsWithoutRef<"code"> & { inline?: boolean }) {
    if (inline) {
      return (
        <code className={`inline-code ${className ?? ""}`.trim()} {...props}>
          {children}
        </code>
      );
    }
    return (
      <code className={className} {...props}>
        {children}
      </code>
    );
  },
  pre({ children }) {
    return <CodeBlock>{children}</CodeBlock>;
  },
};

export const MarkdownMessage = memo(function MarkdownMessage({
  text,
}: MarkdownMessageProps) {
  return (
    <div className="markdown-message">
      <ReactMarkdown remarkPlugins={REMARK_PLUGINS} components={COMPONENTS}>
        {text}
      </ReactMarkdown>
    </div>
  );
});

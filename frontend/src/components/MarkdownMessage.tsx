import { memo, type ComponentPropsWithoutRef } from "react";

import ReactMarkdown, { type Components } from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";

import { useWorkspaceFileLink } from "@/components/WorkspaceFileLinkContext";
import { isWorkspacePathHref, remarkLinkifyPaths } from "@/lib/workspacePaths";

interface MarkdownMessageProps {
  text: string;
}

// Hoisted to module scope so the plugin array and component overrides keep a
// stable identity across renders — combined with the memo() below, an
// unchanged `text` skips the remark parse entirely during streaming.
const REMARK_PLUGINS = [remarkGfm, remarkBreaks, remarkLinkifyPaths];

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
    return <pre className="markdown-pre">{children}</pre>;
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

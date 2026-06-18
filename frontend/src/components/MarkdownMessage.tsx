import { memo, type ComponentPropsWithoutRef } from "react";

import ReactMarkdown, { type Components } from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";

interface MarkdownMessageProps {
  text: string;
}

// Hoisted to module scope so the plugin array and component overrides keep a
// stable identity across renders — combined with the memo() below, an
// unchanged `text` skips the remark parse entirely during streaming.
const REMARK_PLUGINS = [remarkGfm, remarkBreaks];

const COMPONENTS: Components = {
  a({ href, children, ...props }) {
    return (
      <a href={href} rel="noreferrer" target="_blank" {...props}>
        {children}
      </a>
    );
  },
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

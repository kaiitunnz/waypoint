import type { ComponentPropsWithoutRef } from "react";

import ReactMarkdown from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";

interface MarkdownMessageProps {
  text: string;
}

export function MarkdownMessage({ text }: MarkdownMessageProps) {
  return (
    <div className="markdown-message">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkBreaks]}
        components={{
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
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}

import React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";

interface MarkdownTextProps {
  content: string;
  className?: string;
  style?: React.CSSProperties;
  "data-testid"?: string;
}

export function MarkdownText({ content, className, style, ...rest }: MarkdownTextProps) {
  return (
    <div className={className} style={style} {...rest}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        components={{
          p: ({ children }) => <p style={{ margin: "0.25em 0" }}>{children}</p>,
          ul: ({ children }) => (
            <ul style={{ margin: "0.25em 0", paddingLeft: "1.5em" }}>{children}</ul>
          ),
          ol: ({ children }) => (
            <ol style={{ margin: "0.25em 0", paddingLeft: "1.5em" }}>{children}</ol>
          ),
          li: ({ children }) => <li style={{ margin: "0.1em 0" }}>{children}</li>,
          code: ({ className, children, ...props }: React.HTMLAttributes<HTMLElement>) => {
            const isInline = !className;
            return isInline ? (
              <code
                style={{
                  backgroundColor: "rgba(0, 0, 0, 0.06)",
                  padding: "0.1em 0.3em",
                  borderRadius: "0.25em",
                  fontSize: "0.9em",
                }}
                {...props}
              >
                {children}
              </code>
            ) : (
              <code className={className} {...props}>
                {children}
              </code>
            );
          },
          pre: ({ children }) => (
            <pre
              style={{
                backgroundColor: "#f6f8fa",
                padding: "0.75em 1em",
                borderRadius: "0.375em",
                overflowX: "auto",
                fontSize: "0.85em",
                lineHeight: 1.4,
              }}
            >
              {children}
            </pre>
          ),
          a: ({ href, children }) => (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              style={{ color: "#4f46e5", textDecoration: "underline" }}
            >
              {children}
            </a>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

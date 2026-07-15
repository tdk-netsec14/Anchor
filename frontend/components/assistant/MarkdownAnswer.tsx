"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * Renders an answer from the model. Content is untrusted by construction —
 * retrieved documents are attacker-controlled input — so the renderer emits a
 * fixed set of elements, strips raw HTML, and never allows links to carry
 * javascript: targets.
 */
export function MarkdownAnswer({ children }: { children: string }) {
  return (
    <div className="text-[14px] leading-[1.7] text-fg">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ children }) => (
            <h1 className="mb-3 mt-5 text-base font-semibold first:mt-0">{children}</h1>
          ),
          h2: ({ children }) => (
            <h2 className="mb-2 mt-5 text-[15px] font-semibold first:mt-0">{children}</h2>
          ),
          h3: ({ children }) => (
            <h3 className="mb-2 mt-4 text-sm font-semibold first:mt-0">{children}</h3>
          ),
          p: ({ children }) => <p className="mb-3 last:mb-0">{children}</p>,
          ul: ({ children }) => (
            <ul className="mb-3 ml-5 list-disc space-y-1 last:mb-0">{children}</ul>
          ),
          ol: ({ children }) => (
            <ol className="mb-3 ml-5 list-decimal space-y-1 last:mb-0">{children}</ol>
          ),
          li: ({ children }) => <li className="leading-[1.7]">{children}</li>,
          a: ({ children, href }) => (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer nofollow"
              className="text-accent underline underline-offset-2 hover:text-accent-hover"
            >
              {children}
            </a>
          ),
          code: ({ children, className }) =>
            className ? (
              <code className="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[12.5px]">
                {children}
              </code>
            ) : (
              <code className="rounded bg-surface-2 px-1 py-0.5 font-mono text-[12.5px]">
                {children}
              </code>
            ),
          pre: ({ children }) => (
            <pre className="mb-3 overflow-x-auto rounded-lg border border-border bg-surface-2 p-3 font-mono text-[12.5px] leading-relaxed last:mb-0">
              {children}
            </pre>
          ),
          blockquote: ({ children }) => (
            <blockquote className="mb-3 border-l-2 border-border-strong pl-3 text-fg-muted">
              {children}
            </blockquote>
          ),
          table: ({ children }) => (
            <div className="mb-3 overflow-x-auto">
              <table className="w-full border-collapse text-[13px]">{children}</table>
            </div>
          ),
          th: ({ children }) => (
            <th className="border border-border bg-surface-2 px-2.5 py-1.5 text-left font-medium">
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td className="border border-border px-2.5 py-1.5 align-top">{children}</td>
          ),
          hr: () => <hr className="my-4 border-border" />,
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}

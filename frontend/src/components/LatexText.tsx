import { useMemo } from "react";
import katex from "katex";

interface LatexTextProps {
  text: string;
  style?: React.CSSProperties;
  className?: string;
  "data-testid"?: string;
}

function renderLatex(text: string): string {
  const parts: string[] = [];
  let remaining = text;
  let key = 0;

  while (remaining.length > 0) {
    const displayMatch = remaining.match(/\$\$([\s\S]*?)\$\$/);
    const inlineMatch = remaining.match(/\$([^\$\n]+?)\$/);
    let match: RegExpMatchArray | null = null;
    let isDisplay = false;

    if (displayMatch && inlineMatch) {
      const displayIndex = remaining.indexOf(displayMatch[0]);
      const inlineIndex = remaining.indexOf(inlineMatch[0]);
      if (displayIndex <= inlineIndex) {
        match = displayMatch;
        isDisplay = true;
      } else {
        match = inlineMatch;
        isDisplay = false;
      }
    } else if (displayMatch) {
      match = displayMatch;
      isDisplay = true;
    } else if (inlineMatch) {
      match = inlineMatch;
      isDisplay = false;
    }

    if (!match) {
      parts.push(escapeHtml(remaining));
      break;
    }

    const matchIndex = remaining.indexOf(match[0]);
    if (matchIndex > 0) {
      parts.push(escapeHtml(remaining.slice(0, matchIndex)));
    }

    const latex = match[1].trim();
    try {
      const html = katex.renderToString(latex, {
        displayMode: isDisplay,
        throwOnError: false,
      });
      parts.push(
        `<span class="katex-wrapper${isDisplay ? " katex-display" : ""}">${html}</span>`
      );
    } catch {
      parts.push(escapeHtml(match[0]));
    }

    remaining = remaining.slice(matchIndex + match[0].length);
    key++;
  }

  return parts.join("");
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\n/g, "<br/>");
}

export function LatexText({ text, style, className, ...rest }: LatexTextProps) {
  const html = useMemo(() => renderLatex(text), [text]);

  return (
    <div
      style={style}
      className={className}
      dangerouslySetInnerHTML={{ __html: html }}
      {...rest}
    />
  );
}

import { useMemo } from "react";
import katex from "katex";

interface LatexTextProps {
  text: string;
  style?: React.CSSProperties;
  className?: string;
  "data-testid"?: string;
}

function isValidInlineDelimiter(
  text: string,
  matchIndex: number,
  fullMatch: string
): boolean {
  const beforeIndex = matchIndex - 1;
  const afterIndex = matchIndex + fullMatch.length;

  const charBefore = beforeIndex >= 0 ? text[beforeIndex] : null;
  const charAfter = afterIndex < text.length ? text[afterIndex] : null;

  const content = fullMatch.slice(1, -1).trim();
  // Reject currency-like patterns:
  // - Pure integer (e.g., $5$, $10$, $100$)
  // - Decimal with 2 decimal places (typical price format, e.g., $5.00$, $10.50$)
  // - Thousands format (e.g., $1,000$)
  if (
    /^\d+$/.test(content) ||
    /^\d+[.,]\d{2}$/.test(content) ||
    /^\d{1,3},\d{3}/.test(content)
  ) {
    return false;
  }
  if (charBefore !== null && !/[\s({\[,;:!?]/.test(charBefore)) {
    return false;
  }
  if (charAfter !== null && !/[\s)}\].,;:!?]/.test(charAfter)) {
    return false;
  }
  return true;
}

function renderLatex(text: string): string {
  const parts: string[] = [];
  let remaining = text;

  while (remaining.length > 0) {
    const displayMatch = remaining.match(/\$\$([\s\S]*?)\$\$/);
    const inlineMatch = remaining.match(/\$([^\$\n]+?)\$/);
    let match: RegExpMatchArray | null = null;
    let isDisplay = false;
    let skipInline = false;

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

    if (!isDisplay && !isValidInlineDelimiter(remaining, matchIndex, match[0])) {
      skipInline = true;
    }

    if (skipInline) {
      parts.push(escapeHtml(remaining.slice(0, matchIndex + 1)));
      remaining = remaining.slice(matchIndex + 1);
      continue;
    }

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

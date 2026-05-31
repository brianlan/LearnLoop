import { useState } from "react";
import type { CSSProperties } from "react";
import { ProblemImage } from "./ProblemImage";

interface CollapsibleImageProps {
  src?: string | null;
  alt?: string;
  style?: CSSProperties;
}

export function CollapsibleImage({ src, alt = "Problem", style }: CollapsibleImageProps) {
  const [expanded, setExpanded] = useState(false);

  if (!src) {
    return null;
  }

  return (
    <div style={{ marginBottom: "1rem" }}>
      <button
        onClick={() => setExpanded((prev) => !prev)}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: "0.25rem",
          padding: "0.375rem 0.75rem",
          fontSize: "0.875rem",
          color: "var(--color-text-muted)",
          backgroundColor: "var(--color-surface-muted)",
          border: "1px solid var(--color-border)",
          borderRadius: "0.25rem",
          cursor: "pointer",
          marginBottom: expanded ? "0.75rem" : 0,
        }}
      >
        <span
          style={{
            display: "inline-block",
            transition: "transform 0.15s",
            transform: expanded ? "rotate(90deg)" : "rotate(0deg)",
            fontSize: "0.75rem",
          }}
        >
          ▶
        </span>
        {expanded ? "Hide Original Image" : "Show Original Image"}
      </button>
      {expanded && (
        <ProblemImage
          src={src}
          alt={alt}
          style={style}
        />
      )}
    </div>
  );
}
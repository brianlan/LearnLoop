interface TagPillProps {
  tag: string;
}

export function TagPill({ tag }: TagPillProps) {
  return (
    <span
      key={tag}
      style={{
        padding: "0.125rem 0.375rem",
        background: "var(--color-surface-muted)",
        borderRadius: "4px",
        fontSize: "0.75rem",
        display: "inline-flex",
      }}
    >
      {tag}
    </span>
  );
}

interface TagListProps {
  tags: string[];
}

export function TagList({ tags }: TagListProps) {
  if (tags.length === 0) return null;

  return (
    <div
      style={{
        marginTop: "0.5rem",
        display: "flex",
        flexWrap: "wrap",
        gap: "0.375rem",
      }}
    >
      {tags.map((tag) => (
        <TagPill key={tag} tag={tag} />
      ))}
    </div>
  );
}

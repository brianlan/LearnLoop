interface TagPillProps {
  tag: string;
}

export function TagPill({ tag }: TagPillProps) {
  return (
    <span
      key={tag}
      style={{
        padding: "0.2rem 0.6rem",
        background: "var(--color-tag-bg)",
        color: "var(--color-primary-text)",
        borderRadius: "var(--radius-full)",
        fontSize: "0.75rem",
        fontWeight: 600,
        display: "inline-flex",
        border: "1px solid rgba(79, 70, 229, 0.1)",
        transition: "all var(--transition-fast)"
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

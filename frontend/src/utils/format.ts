export function getTimezone(): string {
  try {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    return tz || "UTC";
  } catch {
    return "UTC";
  }
}

export function formatDate(dateString?: string): string {
  if (!dateString) {
    return "—";
  }

  return new Date(dateString).toLocaleString(undefined, {
    timeZone: getTimezone(),
    timeZoneName: "short",
  });
}

export function formatScore(score: number | null): string {
  if (score === null) {
    return "Pending";
  }

  return `${Math.round(score * 100)}%`;
}

export function formatProblemReference(problemId: string): string {
  return problemId.length > 8 ? problemId.slice(0, 8) : problemId;
}

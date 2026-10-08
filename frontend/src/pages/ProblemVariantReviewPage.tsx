import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { BulkDraft, BulkItem } from "@/types/bulkIngestion";
import type { ProblemDetail, ProblemResponse } from "@/types/problem";
import type {
  ProblemVariantMode,
  ProblemVariantSession,
} from "@/types/problemVariants";
import { api } from "@/api/client";
import {
  attestProblemVariant,
  createProblemVariant,
  discardProblemVariant,
  editProblemVariantCandidate,
  generateProblemVariant,
  getActiveProblemVariantSession,
  revalidateProblemVariant,
  submitProblemVariant,
  type ProblemVariantSessionResponse,
} from "@/api/problemVariants";
import { VariantReviewPanel } from "@/components/VariantReviewPanel";
import {
  useBulkReviewEditing,
  type EditTarget,
} from "@/components/BulkReviewStep.editing";
import { getRequiredFieldGaps, isVariantBusy } from "@/components/BulkReviewStep.helpers";

const POLL_INTERVAL_MS = 2000;
const IN_FLIGHT_STATUSES = new Set(["queued", "generating", "validating"]);

// Module-level so the editing hook's reconciliation effect sees a stable
// predicate identity.
function keepFailedCandidateDraft(item: BulkItem): boolean {
  return item.variation?.status === "failed";
}

// One synthetic batch item adapts the session into the exact shape the
// extracted review surface and editing hook consume (issue #685): the
// session's `variation` subtree mirrors `item.variation`, the stored source
// problem plays the confirmed draft, and `contentRevision` maps 1:1.
function sessionToItem(
  session: ProblemVariantSession,
  problem: ProblemDetail | undefined,
): BulkItem {
  const original = session.variation?.original;
  return {
    itemId: session.sessionId,
    imageId: "",
    batchId: "",
    status: "ready",
    order: 0,
    draft: {
      text: original?.text ?? problem?.text ?? "",
      problemType:
        original?.problemType ?? problem?.problemType ?? "short-answer",
      graphDsl: original?.graphDsl ?? problem?.graphDsl ?? "",
      correctAnswer:
        original?.correctAnswer ?? problem?.correctAnswer?.display ?? "",
      tags: session.tags,
      subject: original?.subject ?? "math",
    },
    extraction: {},
    retryCount: 0,
    submit: {},
    origin: {},
    contentRevision: session.contentRevision,
    variation: session.variation,
    createdAt: session.createdAt,
    updatedAt: session.updatedAt,
  };
}

function useProblem(problemId: string) {
  return useQuery({
    queryKey: ["problem", problemId],
    queryFn: async () => {
      const data = await api.get<ProblemResponse>(`/problems/${problemId}`);
      return data.problem;
    },
    enabled: !!problemId,
  });
}

export function ProblemVariantReviewPage() {
  const { id } = useParams<{ id: string }>();
  const problemId = id ?? "";
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [session, setSession] = useState<ProblemVariantSession | null>(null);
  const [isLoadingSession, setIsLoadingSession] = useState(true);
  const [modeChoice, setModeChoice] =
    useState<ProblemVariantMode>("transfer-variant");
  const [entryError, setEntryError] = useState<string | null>(null);
  const [pageError, setPageError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [editTarget, setEditTarget] = useState<EditTarget>("candidate");
  const [recentTags, setRecentTags] = useState<string[]>([]);

  const { data: problem } = useProblem(problemId);

  useEffect(() => {
    let cancelled = false;
    setIsLoadingSession(true);
    getActiveProblemVariantSession(problemId)
      .then((response) => {
        if (!cancelled) setSession(response.session);
      })
      .catch(() => {
        if (!cancelled) setEntryError("Could not load the variant session");
      })
      .finally(() => {
        if (!cancelled) setIsLoadingSession(false);
      });
    return () => {
      cancelled = true;
    };
  }, [problemId]);

  const variationStatus = session?.variation?.status ?? null;
  useEffect(() => {
    // In-flight generation/validation owns the page: poll until it lands
    // (precedent: ProblemDetailPage solution-status polling).
    if (!session || variationStatus === null) return;
    if (!IN_FLIGHT_STATUSES.has(variationStatus)) return;
    const id = window.setInterval(() => {
      getActiveProblemVariantSession(problemId)
        .then((response) => {
          // A session that turned terminal elsewhere (submitted/discarded)
          // is picked up by the explicit submit/discard flows instead.
          if (response.session) setSession(response.session);
        })
        .catch(() => {
          // Polling failures are intentionally not surfaced as blocking.
        });
    }, POLL_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [problemId, session, variationStatus]);

  const applySession = useCallback(
    (response: ProblemVariantSessionResponse) => {
      if (response.session) setSession(response.session);
    },
    [],
  );

  const handleUpdateDraft = useCallback(
    async (
      _itemId: string,
      changes: Partial<BulkDraft>,
      options: { target: EditTarget; expectedRevision: number },
    ) => {
      if (!session) return;
      const onlyTags =
        Object.keys(changes).length === 1 && changes.tags !== undefined;
      // Tag-only saves route through the read-only source target (hook
      // convention), but the source is immutable in this flow: only tags may
      // travel to the status-independent backend branch. Forwarding the full
      // source form through the candidate route would overwrite the stored
      // candidate with source content.
      if (options.target === "source" || onlyTags) {
        const response = await editProblemVariantCandidate(
          problemId,
          session.sessionId,
          { expectedRevision: options.expectedRevision, tags: changes.tags },
        );
        applySession(response);
        const nextItem = response.session
          ? sessionToItem(response.session, problem)
          : undefined;
        if (!nextItem) throw new Error("Saved session missing from response");
        return { item: nextItem };
      }
      const response = await editProblemVariantCandidate(
        problemId,
        session.sessionId,
        {
          expectedRevision: options.expectedRevision,
          text: changes.text ?? undefined,
          problemType: changes.problemType ?? undefined,
          // Empty graphDsl is sent as null: "" vs null counts as a
          // semantic change on the backend (#613 contract).
          graphDsl:
            "graphDsl" in changes ? changes.graphDsl || null : undefined,
          correctAnswer: changes.correctAnswer ?? undefined,
          tags: changes.tags,
        },
      );
      applySession(response);
      const nextItem = response.session
        ? sessionToItem(response.session, problem)
        : undefined;
      if (!nextItem) throw new Error("Saved session missing from response");
      return { item: nextItem };
    },
    [applySession, problem, problemId, session],
  );

  const handleGenerate = useCallback(
    async (_itemId: string, _original: unknown, expectedRevision: number) => {
      if (!session) return;
      // The stored source is immutable: the backend keeps the session's
      // original snapshot, so the reviewed-source payload is ignored.
      const response = await generateProblemVariant(
        problemId,
        session.sessionId,
        expectedRevision,
      );
      applySession(response);
    },
    [applySession, problemId, session],
  );

  const handleRevalidate = useCallback(
    async (_itemId: string, expectedRevision: number) => {
      if (!session) return;
      const response = await revalidateProblemVariant(
        problemId,
        session.sessionId,
        expectedRevision,
      );
      applySession(response);
    },
    [applySession, problemId, session],
  );

  const handleAttest = useCallback(
    async (_itemId: string, expectedRevision: number) => {
      if (!session) return;
      const response = await attestProblemVariant(
        problemId,
        session.sessionId,
        expectedRevision,
      );
      applySession(response);
    },
    [applySession, problemId, session],
  );

  const items = useMemo(
    () => (session ? [sessionToItem(session, problem)] : []),
    [session, problem],
  );
  const {
    getDraft,
    updateDraft,
    handleGenerate: runGenerate,
    handleRevalidate: runRevalidate,
    handleAttest: runAttest,
    actionStateFor,
    hasPendingSaves,
  } = useBulkReviewEditing(
    items,
    {
      onUpdateDraft: handleUpdateDraft,
      onGenerate: handleGenerate,
      onRevalidate: handleRevalidate,
      onAttest: handleAttest,
    },
    // Candidate-from-nothing (#665): a failed session's provisional
    // candidate buffer must survive its first in-flight save.
    { keepCandidateWithoutServerCandidate: keepFailedCandidateDraft },
  );

  const handleCreate = useCallback(async () => {
    setEntryError(null);
    try {
      applySession(await createProblemVariant(problemId, modeChoice));
    } catch (err) {
      if ((err as { code?: string }).code === "VARIANT_SESSION_EXISTS") {
        // A session already exists (race or stale view): enter it.
        applySession(await getActiveProblemVariantSession(problemId));
        return;
      }
      setEntryError(
        err instanceof Error
          ? err.message
          : "Could not create the variant session",
      );
    }
  }, [applySession, modeChoice, problemId]);

  const handleSubmit = useCallback(async () => {
    if (!session) return;
    setIsSubmitting(true);
    setSubmitError(null);
    try {
      const result = await submitProblemVariant(problemId, session.sessionId);
      queryClient.invalidateQueries({ queryKey: ["problems"] });
      queryClient.invalidateQueries({ queryKey: ["tags"] });
      navigate(`/problems/${result.problemId}`);
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : "Submit failed");
    } finally {
      setIsSubmitting(false);
    }
  }, [navigate, problemId, queryClient, session]);

  const handleDiscard = useCallback(async () => {
    if (!session) return;
    try {
      await discardProblemVariant(problemId, session.sessionId);
      navigate(`/problems/${problemId}`);
    } catch (err) {
      setPageError(err instanceof Error ? err.message : "Discard failed");
    }
  }, [navigate, problemId, session]);

  const item = items[0];
  // A failed session with no candidate still supports candidate-from-nothing
  // recovery (#665): the PATCH creates the candidate from the edited fields.
  const activeTarget: EditTarget =
    item?.variation?.candidate || item?.variation?.status === "failed"
      ? editTarget
      : "source";

  const handleTagsChange = useCallback(
    (nextTags: string[], prevTags: string[]) => {
      const prevSet = new Set(prevTags);
      const added = nextTags.filter((tag) => !prevSet.has(tag));
      if (added.length > 0) {
        setRecentTags((prev) => {
          const seen = new Set(prev);
          const next = [...prev];
          for (const tag of added) {
            if (seen.has(tag)) continue;
            seen.add(tag);
            next.unshift(tag);
          }
          return next.slice(0, 5);
        });
      }
      if (item) updateDraft(item, activeTarget, { tags: nextTags });
    },
    [activeTarget, item, updateDraft],
  );

  if (isLoadingSession) {
    return (
      <div className="page" data-testid="problem-variant-review">
        <p>Loading…</p>
      </div>
    );
  }

  if (!session) {
    return (
      <div className="page" data-testid="problem-variant-review">
        <h2>Create variant</h2>
        <p style={{ color: "var(--color-text-muted)" }}>
          Generate a variant problem from this problem. The source problem is
          never modified.
        </p>
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: "8px",
            maxWidth: "360px",
          }}
        >
          <label>
            <input
              type="radio"
              name="variant-mode"
              data-testid="variant-mode-transfer"
              checked={modeChoice === "transfer-variant"}
              onChange={() => setModeChoice("transfer-variant")}
            />{" "}
            Transfer variant (new surface, same answer)
          </label>
          <label>
            <input
              type="radio"
              name="variant-mode"
              data-testid="variant-mode-data-only"
              checked={modeChoice === "data-only"}
              onChange={() => setModeChoice("data-only")}
            />{" "}
            Data-only variant (same wording, new data)
          </label>
          <button
            type="button"
            data-testid="variant-create-start"
            className="btn btn-primary"
            onClick={handleCreate}
          >
            Start variant
          </button>
          {entryError && (
            <span
              data-testid="variant-entry-error"
              style={{ color: "var(--color-error, #dc2626)" }}
            >
              {entryError}
            </span>
          )}
        </div>
      </div>
    );
  }

  if (!item) {
    // Unreachable: a non-null session always adapts to one item.
    return null;
  }

  const currentDraft = getDraft(item, activeTarget);
  const bulkActionState = actionStateFor(item, true, false);
  // The backend Generate endpoint overrides an in-flight attempt (self-heals
  // a session stranded by a restart), so the reused bulk-review "still
  // running" disable must not block the button here.
  const actionState =
    item.variation && isVariantBusy(item.variation.status)
      ? { ...bulkActionState, generateDisabledReason: "" }
      : bulkActionState;
  const isReady = item.variation?.status === "ready";
  const tagSuggestions = problem?.tags ?? [];

  return (
    <div className="page" data-testid="problem-variant-review">
      <h2>Create variant</h2>
      <p style={{ color: "var(--color-text-muted)" }}>
        {item.variation?.status === "failed"
          ? "The last attempt failed. Fix the candidate below or generate again."
          : "Review the generated variant, then submit to admit it as a new problem."}
      </p>
      {pageError && (
        <div
          data-testid="variant-page-error"
          style={{ color: "var(--color-error, #dc2626)" }}
        >
          {pageError}
        </div>
      )}
      <VariantReviewPanel
        status="ready"
        failureMessage={null}
        variation={item.variation}
        draft={currentDraft}
        requiredFieldGaps={getRequiredFieldGaps(currentDraft)}
        imageSource={problem?.imageUrl ?? ""}
        target={activeTarget}
        onTargetChange={setEditTarget}
        isFieldDisabled={false}
        sourceReadOnly
        showGenerate
        actionState={actionState}
        onUpdateDraft={(changes) => updateDraft(item, activeTarget, changes)}
        onGenerate={() => runGenerate(item)}
        onRevalidate={() => runRevalidate(item)}
        onAttest={() => runAttest(item)}
        reviewTagSuggestions={tagSuggestions}
        recentTags={recentTags}
        onTagsChange={handleTagsChange}
        extraActions={
          <>
            {isReady && (
              <button
                type="button"
                data-testid="variant-submit"
                className="btn btn-primary"
                onClick={handleSubmit}
                disabled={isSubmitting || actionState.hasConflict || hasPendingSaves}
                title={
                  actionState.hasConflict
                    ? "Resolve the draft conflict first"
                    : hasPendingSaves
                      ? "Saving your edits…"
                      : "Admit this variant as a new problem"
                }
              >
                {isSubmitting ? "Submitting..." : "Submit"}
              </button>
            )}
            <button
              type="button"
              data-testid="variant-discard"
              className="btn btn-secondary"
              onClick={handleDiscard}
              disabled={isSubmitting}
            >
              Discard
            </button>
          </>
        }
      />
      {submitError && (
        <div
          data-testid="variant-submit-error"
          style={{
            color: "var(--color-error, #dc2626)",
            marginTop: "12px",
          }}
        >
          {submitError}
        </div>
      )}
    </div>
  );
}

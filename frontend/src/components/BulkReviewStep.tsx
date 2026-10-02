import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  BulkBatch,
  BulkDraft,
  BulkItem,
  VariationOriginalPayload,
} from "@/types/bulkIngestion";
import { TagInput } from "./TagInput";
import { GraphSandbox } from "./GraphSandbox";
import { LatexText } from "./LatexText";
import {
  bufferKey,
  canAttestVariant,
  evidenceChecks,
  evidenceView,
  failureKindLabel,
  getRequiredFieldGaps,
  hasActiveVariantWork,
  isStaleStamp,
  isVariantBusy,
  retryDelayMs,
  serializeDraft,
  sourcePayloadFromDraft,
  statusLabel,
  targetDraft,
  targetStamp,
  variantPassGateReason,
  variationStatusLabel,
  type EditTarget,
  type TargetStamp,
} from "./BulkReviewStep.helpers";

const POLL_INTERVAL_MS = 2500;
const ACTION_REQUIRED_BORDER = "2px solid var(--color-error, #dc2626)";
const TARGETS: EditTarget[] = ["source", "candidate"];

const PROBLEM_TYPES = [
  { value: "single-choice", label: "Single choice" },
  { value: "multi-choice", label: "Multiple choice" },
  { value: "fill-in-the-blank", label: "Fill in the blank" },
  { value: "short-answer", label: "Short answer" },
];

const SUBJECTS = [
  { value: "math", label: "Math" },
  { value: "english", label: "English" },
];

// Read-only structured failure evidence for a failed variant run (#613
// report payload). No accept/override/fallback controls exist here.
function VariationFailureEvidence({
  variation,
}: {
  variation: NonNullable<BulkItem["variation"]>;
}) {
  const validation = evidenceView(variation.validation);
  const failures = validation?.failures ?? [];
  const reports = validation?.reports ?? [];
  return (
    <div
      data-testid="bulk-review-evidence"
      style={{
        border: "1px solid var(--color-border)",
        borderRadius: "6px",
        padding: "12px",
        marginBottom: "12px",
        display: "flex",
        flexDirection: "column",
        gap: "8px",
        fontSize: "0.9em",
      }}
    >
      <div style={{ fontWeight: 600 }}>Generation evidence (read-only)</div>
      {variation.status === "ready" && variation.attestation && (
        <div
          data-testid="bulk-review-attestation"
          style={{ color: "var(--color-warning, #b45309)" }}
        >
          User-attested at revision {variation.attestation.revision} — kept by
          the teacher, not covered by a validator run.
          {failures.length > 0 &&
            " Waved checks are listed below; the teacher accepted them as-is."}
        </div>
      )}
      <div data-testid="bulk-review-evidence-types">
        Source type: {variation.original?.problemType ?? "unknown"} · Candidate
        type: {variation.candidate?.problemType ?? "unknown"}
      </div>
      <div data-testid="bulk-review-evidence-answers">
        Expected answer (source): {variation.original?.correctAnswer ?? "—"} ·
        Expected answer (candidate): {variation.candidate?.correctAnswer ?? "—"}
      </div>
      {failures.map((failure, index) => (
        <div key={index} data-testid="bulk-review-evidence-failure">
          <strong data-testid="bulk-review-evidence-failure-kind">
            {failureKindLabel(failure.kind)}
          </strong>
          : {failure.evidence}
        </div>
      ))}
      {reports.map((report, index) => (
        <div key={index} data-testid="bulk-review-evidence-report">
          <div data-testid="bulk-review-evidence-solved">
            Solved (original): {report.originalSolvedAnswer ?? "—"} · Solved
            (variant): {report.variantSolvedAnswer ?? "—"}
          </div>
          <div data-testid="bulk-review-evidence-helper-original">
            Helper {report.validatorModel?.provider ?? "?"} /{" "}
            {report.validatorModel?.model ?? "?"} (original):{" "}
            {report.answerComparisonOriginal?.result ?? "no judgement"}
            {report.answerComparisonOriginal?.evidence
              ? ` — ${report.answerComparisonOriginal.evidence}`
              : ""}
          </div>
          <div data-testid="bulk-review-evidence-helper-variant">
            Helper {report.validatorModel?.provider ?? "?"} /{" "}
            {report.validatorModel?.model ?? "?"} (variant):{" "}
            {report.answerComparisonVariant?.result ?? "no judgement"}
            {report.answerComparisonVariant?.evidence
              ? ` — ${report.answerComparisonVariant.evidence}`
              : ""}
          </div>
          <ul data-testid="bulk-review-evidence-checks" style={{ margin: 0 }}>
            {evidenceChecks(report).map((check) => (
              <li key={check.category}>
                {check.category}: {check.evidence}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

export interface BulkReviewStepProps {
  batch: BulkBatch;
  isLoading: boolean;
  onRefresh: (batchId: string) => void | Promise<void>;
  onUpdateDraft: (
    itemId: string,
    changes: Partial<BulkDraft>,
    options: { target: EditTarget; expectedRevision: number },
  ) =>
    | void
    | { contentRevision: number }
    | Promise<void | { contentRevision: number }>;
  onGenerate: (
    itemId: string,
    original: VariationOriginalPayload,
    expectedRevision: number,
  ) => void | Promise<void>;
  onRevalidate: (
    itemId: string,
    expectedRevision: number,
  ) => void | Promise<void>;
  onAttest: (
    itemId: string,
    expectedRevision: number,
  ) => void | Promise<void>;
  onRetry: (itemId: string) => void | Promise<void>;
  onDelete: (itemId: string) => void | Promise<void>;
  onUndoDelete: (itemId: string) => void | Promise<void>;
  onContinue: () => void;
  tagSuggestions?: string[];
}

export function BulkReviewStep({
  batch,
  isLoading,
  onRefresh,
  onUpdateDraft,
  onGenerate,
  onRevalidate,
  onAttest,
  onRetry,
  onDelete,
  onUndoDelete,
  onContinue,
  tagSuggestions = [],
}: BulkReviewStepProps) {
  const items = useMemo(
    () => [...batch.items].sort((a, b) => a.order - b.order),
    [batch.items],
  );
  const [selectedItemId, setSelectedItemId] = useState<string>(() => {
    const firstActionable = items.find((item) => item.status !== "deleted");
    return firstActionable?.itemId ?? items[0]?.itemId ?? "";
  });
  // Autosave buffers are keyed by itemId AND target so source and candidate
  // edits never share content, dirty state, or in-flight save identity.
  const [localDrafts, setLocalDrafts] = useState<Record<string, BulkDraft>>({});
  const [dirtyKeys, setDirtyKeys] = useState<Set<string>>(new Set());
  const [savingKeys, setSavingKeys] = useState<Set<string>>(new Set());
  const [saveFailures, setSaveFailures] = useState<Record<string, number>>({});
  // Preferred editing target when the selected item has a candidate.
  const [editTarget, setEditTarget] = useState<EditTarget>("candidate");
  const [recentTags, setRecentTags] = useState<string[]>([]);
  const draftRefs = useRef<Record<string, BulkDraft>>({});
  const dirtyRefs = useRef<Set<string>>(new Set());
  const saveFailuresRef = useRef<Record<string, number>>({});
  const serverDraftRefs = useRef<Record<string, string>>({});
  const stampRefs = useRef<Record<string, TargetStamp>>({});
  const inFlightRefs = useRef<
    Record<
      string,
      | {
          seq: number;
          revision: number;
          generation: number;
        }
      | undefined
    >
  >({});
  const saveSeqRef = useRef(0);
  // Generate confirms the reviewed source at click time and fires only after
  // that save settles: never a stale stored answer, never a race with the
  // debounce. Pending Generate is keyed by the SOURCE save key so only that
  // save's completion can release or cancel it — a candidate save of the same
  // item must not. One in-flight/pending generate per item ("exactly once").
  const [generatingIds, setGeneratingIds] = useState<Set<string>>(new Set());
  const [generateErrors, setGenerateErrors] = useState<Record<string, string>>(
    {},
  );
  const pendingGenerateRef = useRef<Map<string, VariationOriginalPayload>>(
    new Map(),
  );
  // Validator-only revalidation for needs-validation candidates (#613 edit
  // contract): never a generate call, never a generationCount change.
  const [revalidatingIds, setRevalidatingIds] = useState<Set<string>>(
    new Set(),
  );
  const [revalidateErrors, setRevalidateErrors] = useState<
    Record<string, string>
  >({});
  // Keep-validation attestation (#648): explicit teacher acceptance of a
  // stale PASS instead of paying for revalidation. Same fencing as
  // revalidate: wait for pending candidate saves to settle.
  const [attestingIds, setAttestingIds] = useState<Set<string>>(new Set());
  const [attestErrors, setAttestErrors] = useState<Record<string, string>>({});

  const selectedItem = useMemo(
    () => items.find((item) => item.itemId === selectedItemId) || items[0],
    [items, selectedItemId],
  );

  const activeTarget: EditTarget = selectedItem?.variation?.candidate
    ? editTarget
    : "source";
  const activeKey = selectedItem
    ? bufferKey(selectedItem.itemId, activeTarget)
    : "";

  const getDraft = useCallback(
    (item: BulkItem, target: EditTarget): BulkDraft => {
      return localDrafts[bufferKey(item.itemId, target)] ?? targetDraft(item, target);
    },
    [localDrafts],
  );

  const firePendingGenerate = useCallback(
    (key: string, revision: number) => {
      const original = pendingGenerateRef.current.get(key);
      if (!original) return;
      pendingGenerateRef.current.delete(key);
      const itemId = key.split("::")[0];
      Promise.resolve(onGenerate(itemId, original, revision))
        .catch((err: unknown) => {
          setGenerateErrors((prev) => ({
            ...prev,
            [itemId]: err instanceof Error ? err.message : "Generate failed",
          }));
        })
        .finally(() => {
          setGeneratingIds((prev) => {
            const next = new Set(prev);
            next.delete(itemId);
            return next;
          });
        });
    },
    [onGenerate],
  );

  const handleGenerate = useCallback(
    (item: BulkItem) => {
      const { itemId } = item;
      const sourceKey = bufferKey(itemId, "source");
      if (
        generatingIds.has(itemId) ||
        pendingGenerateRef.current.has(sourceKey)
      ) {
        return;
      }
      // Snapshot the reviewed source at click time.
      pendingGenerateRef.current.set(
        sourceKey,
        sourcePayloadFromDraft(getDraft(item, "source")),
      );
      setGenerateErrors((prev) => {
        if (prev[itemId] === undefined) return prev;
        const next = { ...prev };
        delete next[itemId];
        return next;
      });
      setGeneratingIds((prev) => new Set(prev).add(itemId));
      if (!dirtyKeys.has(sourceKey) && !savingKeys.has(sourceKey)) {
        firePendingGenerate(sourceKey, item.contentRevision);
      }
      // Otherwise the save pipeline fires it once the reviewed save settles.
    },
    [dirtyKeys, firePendingGenerate, getDraft, generatingIds, savingKeys],
  );

  const handleRevalidate = useCallback(
    (item: BulkItem) => {
      const { itemId } = item;
      if (revalidatingIds.has(itemId)) return;
      setRevalidateErrors((prev) => {
        if (prev[itemId] === undefined) return prev;
        const next = { ...prev };
        delete next[itemId];
        return next;
      });
      setRevalidatingIds((prev) => new Set(prev).add(itemId));
      Promise.resolve(onRevalidate(itemId, item.contentRevision))
        .catch((err: unknown) => {
          setRevalidateErrors((prev) => ({
            ...prev,
            [itemId]:
              err instanceof Error ? err.message : "Revalidate failed",
          }));
        })
        .finally(() => {
          setRevalidatingIds((prev) => {
            const next = new Set(prev);
            next.delete(itemId);
            return next;
          });
        });
    },
    [onRevalidate, revalidatingIds],
  );

  const handleAttest = useCallback(
    (item: BulkItem) => {
      const { itemId } = item;
      if (attestingIds.has(itemId)) return;
      setAttestErrors((prev) => {
        if (prev[itemId] === undefined) return prev;
        const next = { ...prev };
        delete next[itemId];
        return next;
      });
      setAttestingIds((prev) => new Set(prev).add(itemId));
      Promise.resolve(onAttest(itemId, item.contentRevision))
        .catch((err: unknown) => {
          setAttestErrors((prev) => ({
            ...prev,
            [itemId]: err instanceof Error ? err.message : "Attest failed",
          }));
        })
        .finally(() => {
          setAttestingIds((prev) => {
            const next = new Set(prev);
            next.delete(itemId);
            return next;
          });
        });
    },
    [onAttest, attestingIds],
  );

  const reviewTagSuggestions = useMemo(() => {
    const seen = new Set<string>();
    const merged: string[] = [];

    const addTag = (tag: string) => {
      const trimmed = tag.trim();
      if (!trimmed || seen.has(trimmed)) return;
      seen.add(trimmed);
      merged.push(trimmed);
    };

    for (const tag of tagSuggestions) {
      addTag(tag);
    }
    for (const item of items) {
      for (const target of TARGETS) {
        if (target === "candidate" && !item.variation?.candidate) continue;
        for (const tag of getDraft(item, target).tags ?? []) {
          addTag(tag);
        }
      }
    }

    return merged;
  }, [getDraft, items, tagSuggestions]);

  const updateDraft = useCallback(
    (key: string, next: Partial<BulkDraft>) => {
      const merged = {
        ...draftRefs.current,
        [key]: { ...draftRefs.current[key], ...next },
      };
      draftRefs.current = merged;
      setLocalDrafts(merged);
      setDirtyKeys((prev) => {
        const nextSet = new Set(prev);
        nextSet.add(key);
        dirtyRefs.current = nextSet;
        return nextSet;
      });
    },
    [],
  );

  const handleTagsChange = useCallback(
    (key: string, nextTags: string[], prevTags: string[]) => {
      const prevSet = new Set(prevTags);
      const added = nextTags.filter((tag) => !prevSet.has(tag));
      if (added.length > 0) {
        setRecentTags((prev) => {
          const next = [...prev];
          const seen = new Set(prev);
          for (const tag of added) {
            if (seen.has(tag)) {
              const index = next.indexOf(tag);
              if (index >= 0) next.splice(index, 1);
            } else {
              seen.add(tag);
            }
            next.unshift(tag);
          }
          return next.slice(0, 5);
        });
      }
      updateDraft(key, { tags: nextTags });
    },
    [updateDraft],
  );

  useEffect(() => {
    if (!selectedItem) return;
    const key = bufferKey(selectedItem.itemId, activeTarget);
    if (draftRefs.current[key] !== undefined) return;
    const merged = {
      ...draftRefs.current,
      [key]: targetDraft(selectedItem, activeTarget),
    };
    draftRefs.current = merged;
    setLocalDrafts(merged);
  }, [selectedItem, activeTarget]);

  useEffect(() => {
    let nextDrafts: Record<string, BulkDraft> | undefined;

    for (const item of items) {
      for (const target of TARGETS) {
        if (target === "candidate" && !item.variation?.candidate) continue;
        const key = bufferKey(item.itemId, target);
        const incoming = targetStamp(item, target);
        const previousStamp = stampRefs.current[key];
        // A stale save/poll response never replaces newer state.
        if (isStaleStamp(incoming, previousStamp)) continue;
        stampRefs.current[key] = incoming;

        const serverDraft = targetDraft(item, target);
        const serializedServerDraft = serializeDraft(serverDraft);
        const previousServerDraft = serverDraftRefs.current[key];
        serverDraftRefs.current[key] = serializedServerDraft;

        // A regenerated candidate replaces the buffer entirely: local edits
        // and save failures belong to the dead candidate.
        const generationChanged =
          target === "candidate" &&
          previousStamp !== undefined &&
          incoming.generation !== previousStamp.generation;

        if (!generationChanged && previousServerDraft === serializedServerDraft) {
          continue;
        }
        if (
          !generationChanged &&
          (dirtyRefs.current.has(key) ||
            savingKeys.has(key) ||
            saveFailuresRef.current[key] !== undefined)
        ) {
          continue;
        }

        if (nextDrafts === undefined) {
          nextDrafts = { ...draftRefs.current };
        }
        nextDrafts[key] = serverDraft;

        if (generationChanged) {
          const nextDirty = new Set(dirtyRefs.current);
          nextDirty.delete(key);
          dirtyRefs.current = nextDirty;
          setDirtyKeys(nextDirty);
          setSaveFailures((prev) => {
            if (prev[key] === undefined) return prev;
            const next = { ...prev };
            delete next[key];
            saveFailuresRef.current = next;
            return next;
          });
        }
      }
    }

    if (nextDrafts !== undefined) {
      draftRefs.current = nextDrafts;
      setLocalDrafts(nextDrafts);
    }
  }, [items, savingKeys]);

  useEffect(() => {
    const timeoutIds: Record<string, number> = {};

    const finishSave = (
      key: string,
      seq: number,
      outcome: "success" | "failure",
      sentSerialized: string,
      result?: void | { contentRevision: number },
    ) => {
      const sent = inFlightRefs.current[key];
      if (!sent || sent.seq !== seq) return;
      inFlightRefs.current[key] = undefined;
      setSavingKeys((prev) => {
        const next = new Set(prev);
        next.delete(key);
        return next;
      });

      // Response-ordering guard keyed by target + version: a response for an
      // older candidate version is ignored, newer state wins.
      const stamp = stampRefs.current[key];
      if (
        stamp &&
        (stamp.revision !== sent.revision ||
          stamp.generation !== sent.generation)
      ) {
        return;
      }

      if (outcome === "success") {
        setSaveFailures((prev) => {
          if (prev[key] === undefined) return prev;
          const next = { ...prev };
          delete next[key];
          saveFailuresRef.current = next;
          return next;
        });
        setDirtyKeys((prevDirty) => {
          const nextDirty = new Set(prevDirty);
          if (
            JSON.stringify(draftRefs.current[key]) === sentSerialized
          ) {
            nextDirty.delete(key);
          }
          dirtyRefs.current = nextDirty;
          return nextDirty;
        });
        // The reviewed SOURCE save settled: confirm the queued Generate with
        // the post-save revision (falls back to the sent revision for saves
        // that do not bump contentRevision, e.g. tag-only edits). Candidate
        // saves pass their own key and never match the pending entry.
        firePendingGenerate(
          key,
          result && typeof result === "object"
            ? result.contentRevision
            : sent.revision,
        );
      } else {
        // A failed SOURCE save prevents a stale Generate; a failed candidate
        // save cannot cancel it.
        if (pendingGenerateRef.current.delete(key)) {
          setGeneratingIds((prev) => {
            const next = new Set(prev);
            next.delete(key.split("::")[0]);
            return next;
          });
        }
        setSaveFailures((prev) => {
          const next = { ...prev, [key]: (prev[key] ?? 0) + 1 };
          saveFailuresRef.current = next;
          return next;
        });
      }
    };

    const scheduleSave = (key: string) => {
      window.clearTimeout(timeoutIds[key]);
      const failures = saveFailuresRef.current[key] ?? 0;
      timeoutIds[key] = window.setTimeout(() => {
        const draft = draftRefs.current[key];
        if (!draft) return;
        const sentDraft = JSON.parse(JSON.stringify(draft)) as BulkDraft;
        const sentSerialized = JSON.stringify(sentDraft);
        const stamp = stampRefs.current[key];
        const seq = (saveSeqRef.current += 1);
        inFlightRefs.current[key] = {
          seq,
          revision: stamp?.revision ?? 0,
          generation: stamp?.generation ?? 0,
        };
        setSavingKeys((prev) => {
          const next = new Set(prev);
          next.add(key);
          return next;
        });
        const [itemId, target] = key.split("::") as [string, EditTarget];
        Promise.resolve(
          onUpdateDraft(itemId, sentDraft, {
            target,
            expectedRevision: stamp?.revision ?? 0,
          }),
        )
          .then((result) => finishSave(key, seq, "success", sentSerialized, result))
          .catch(() => finishSave(key, seq, "failure", sentSerialized));
      }, retryDelayMs(failures));
    };

    dirtyKeys.forEach((key) => {
      if (!savingKeys.has(key)) {
        scheduleSave(key);
      }
    });

    return () => {
      Object.values(timeoutIds).forEach((id) => window.clearTimeout(id));
    };
  }, [dirtyKeys, savingKeys, firePendingGenerate, onUpdateDraft]);

  useEffect(() => {
    // Extraction and variant work both keep the batch being observed.
    const hasActiveWork = items.some(
      (item) =>
        item.status === "queued" ||
        item.status === "extracting" ||
        hasActiveVariantWork(item),
    );
    if (!hasActiveWork || batch.status !== "active") return;

    const id = window.setInterval(() => {
      onRefresh(batch.id);
    }, POLL_INTERVAL_MS);

    return () => window.clearInterval(id);
  }, [batch.id, batch.status, items, onRefresh]);

  const selectedIndex = items.findIndex(
    (item) => item.itemId === selectedItem?.itemId,
  );

  const navigate = useCallback(
    (direction: -1 | 1) => {
      const nextIndex = selectedIndex + direction;
      if (nextIndex >= 0 && nextIndex < items.length) {
        setSelectedItemId(items[nextIndex].itemId);
      }
    },
    [items, selectedIndex],
  );

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (!event.altKey) return;
      if (event.key === "PageDown") {
        navigate(1);
        event.preventDefault();
      } else if (event.key === "PageUp") {
        navigate(-1);
        event.preventDefault();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [navigate]);

  if (!selectedItem) {
    return (
      <div data-testid="bulk-wizard-review-step">
        <h2>Review extracted items</h2>
        <p>No items to review.</p>
      </div>
    );
  }

  const sourceImage = batch.images.find(
    (image) => image.imageId === selectedItem.imageId,
  );
  const previewUrl = selectedItem.crop?.mediaUrl || sourceImage?.sourceImage?.mediaUrl || "";
  const isDeleted = selectedItem.status === "deleted";
  const isEditable =
    !isDeleted &&
    selectedItem.status !== "queued" &&
    selectedItem.status !== "extracting" &&
    selectedItem.status !== "submitted";
  const currentDraft = getDraft(selectedItem, activeTarget);
  const activeKeyPrefix = `${selectedItem.itemId}::`;
  const isActionWorking =
    isLoading ||
    [...savingKeys].some((key) => key.startsWith(activeKeyPrefix));
  const isFieldDisabled = !isEditable || isLoading;
  const hasSaveFailed = Object.keys(saveFailures).some((key) =>
    key.startsWith(activeKeyPrefix),
  );
  const variation = selectedItem.variation;
  const variationBusy = variation ? isVariantBusy(variation.status) : false;
  const isGenerating = generatingIds.has(selectedItem.itemId);
  const sourceKey = bufferKey(selectedItem.itemId, "source");
  const sourceGaps = getRequiredFieldGaps(getDraft(selectedItem, "source"));
  const sourceSaveFailed = saveFailures[sourceKey] !== undefined;
  const sourceSavePending =
    dirtyKeys.has(sourceKey) || savingKeys.has(sourceKey);
  let generateDisabledReason = "";
  if (!isEditable) {
    generateDisabledReason = "Item is not editable";
  } else if (sourceGaps.text || sourceGaps.problemType || sourceGaps.correctAnswer) {
    generateDisabledReason =
      "Source needs text, problem type and a confirmed answer";
  } else if (sourceSaveFailed) {
    generateDisabledReason = "Draft save failed, retrying";
  } else if (isGenerating) {
    generateDisabledReason = "Generating...";
  } else if (variationBusy) {
    generateDisabledReason = "Variant work is still running";
  }
  const generateHint =
    generateDisabledReason ||
    (sourceSavePending
      ? "Generate confirms the reviewed source once its save settles"
      : "");
  const generateError = generateErrors[selectedItem.itemId];
  const revalidating = revalidatingIds.has(selectedItem.itemId);
  const revalidateError = revalidateErrors[selectedItem.itemId];
  const attesting = attestingIds.has(selectedItem.itemId);
  const attestError = attestErrors[selectedItem.itemId];
  // Revalidate/attest send the current contentRevision; an unsent/in-flight
  // candidate save would land after either and self-invalidate, so wait
  // for it. Both actions share the identical gating.
  const candidateKey = bufferKey(selectedItem.itemId, "candidate");
  const revalidatePendingSave =
    dirtyKeys.has(candidateKey) || savingKeys.has(candidateKey);
  const variationNeedsValidation = variation?.status === "needs-validation";
  const revalidateDisabledReason = !variationNeedsValidation
    ? ""
    : !isEditable
      ? "Item is not editable"
      : isActionWorking
        ? "Draft save is still settling"
        : revalidatePendingSave
          ? "Candidate changes are still saving"
          : "";
  // The stale PASS report is the attest precondition, mirroring the backend
  // attest predicate (needs-validation + stored verdict pass).
  const stalePassReport =
    variationNeedsValidation && variation?.validation?.verdict === "pass";
  // #658: a FAIL whose failures are all check-kind may be overridden by
  // teacher attestation, from needs-validation or failed.
  const failOverrideEligible =
    canAttestVariant(selectedItem) && variation?.validation?.verdict === "fail";
  const failedItemIds = new Set(
    Object.keys(saveFailures).map((key) => key.split("::")[0]),
  );
  const activeItems = items.filter((item) => item.status !== "deleted");
  const itemValidation = activeItems.map((item) => {
    const draft = getDraft(item, "source");
    const requiredFieldGaps = getRequiredFieldGaps(draft);
    // Submitted items are done; they must not block the remaining flow.
    if (item.status === "submitted") {
      return { itemId: item.itemId, reasons: [] as string[], requiredFieldGaps };
    }
    const reasons: string[] = [];
    if (item.status === "queued" || item.status === "extracting") {
      reasons.push(`Item ${item.order + 1}: Extraction is still running`);
    } else if (item.status === "failed") {
      reasons.push(`Item ${item.order + 1}: Extraction failed`);
    } else if (item.status !== "ready" && item.status !== "submit-failed") {
      reasons.push(`Item ${item.order + 1}: Item is not ready`);
    }
    if (!draft.text || draft.text.trim() === "") {
      reasons.push(`Item ${item.order + 1}: Question text is required`);
    }
    if (!draft.problemType) {
      reasons.push(`Item ${item.order + 1}: Problem type is required`);
    }
    if (!draft.correctAnswer || draft.correctAnswer.trim() === "") {
      reasons.push(`Item ${item.order + 1}: Correct answer is required`);
    }
    if (batch.ingestionMode !== "original") {
      // Only current-PASS candidates gate Continue.
      const variantReason = variantPassGateReason(item);
      if (variantReason) {
        reasons.push(`Item ${item.order + 1}: ${variantReason}`);
      }
    }
    return { itemId: item.itemId, reasons, requiredFieldGaps };
  });
  const itemValidationById = new Map(
    itemValidation.map((validation) => [validation.itemId, validation]),
  );
  const selectedValidation = itemValidationById.get(selectedItem.itemId);
  const selectedRequiredFieldGaps =
    selectedValidation?.requiredFieldGaps ?? getRequiredFieldGaps(currentDraft);
  const continueDisabledReasons = itemValidation.flatMap(
    (validation) => validation.reasons,
  );
  if (activeItems.length === 0) {
    continueDisabledReasons.push("No items to submit");
  }
  if (dirtyKeys.size > 0 || savingKeys.size > 0) {
    continueDisabledReasons.push("Draft changes are still saving");
  }
  if (Object.keys(saveFailures).length > 0) {
    continueDisabledReasons.push("Draft save failed, retrying");
  }
  const canContinue = continueDisabledReasons.length === 0;

  const currentTagSet = new Set(currentDraft.tags ?? []);
  const visibleRecentTags = recentTags.filter((tag) => !currentTagSet.has(tag));

  const handleRecentTagClick = (tag: string) => {
    if (isFieldDisabled) return;
    const currentTags = currentDraft.tags ?? [];
    if (currentTags.includes(tag)) return;
    handleTagsChange(activeKey, [...currentTags, tag], currentTags);
  };

  return (
    <div data-testid="bulk-wizard-review-step">
      <h2>Review extracted items</h2>

      <div
        style={{
          display: "flex",
          gap: "16px",
          marginBottom: "16px",
        }}
      >
        <button
          type="button"
          data-testid="bulk-review-prev"
          onClick={() => navigate(-1)}
          disabled={selectedIndex <= 0}
        >
          Previous
        </button>
        <span data-testid="bulk-review-position">
          {selectedIndex + 1} / {items.length}
        </span>
        <button
          type="button"
          data-testid="bulk-review-next"
          onClick={() => navigate(1)}
          disabled={selectedIndex >= items.length - 1}
        >
          Next
        </button>
      </div>

      <div
        data-testid="bulk-review-layout"
        style={{
          display: "grid",
          gridTemplateColumns: "120px 1fr",
          gap: "16px",
        }}
      >
        <div>
          <h3>Items</h3>
          <ul data-testid="bulk-review-queue" style={{ padding: 0, listStyle: "none" }}>
            {items.map((item) => (
              <li key={item.itemId}>
                <button
                  type="button"
                  data-testid={`bulk-review-item-${item.itemId}`}
                  data-action-required={
                    (itemValidationById.get(item.itemId)?.reasons.length ?? 0) > 0
                      ? "true"
                      : "false"
                  }
                  onClick={() => setSelectedItemId(item.itemId)}
                  disabled={item.itemId === selectedItem.itemId}
                  style={{
                    width: "100%",
                    textAlign: "left",
                    border:
                      (itemValidationById.get(item.itemId)?.reasons.length ?? 0) > 0
                        ? ACTION_REQUIRED_BORDER
                        : "2px solid transparent",
                    borderRadius: "6px",
                    background:
                      item.itemId === selectedItem.itemId
                        ? "var(--color-primary)"
                        : "transparent",
                    color:
                      item.itemId === selectedItem.itemId ? "white" : "inherit",
                  }}
                >
                  {item.order + 1}. {statusLabel(item.status)}
                  {item.variation && (
                    <span
                      data-testid={`bulk-review-item-variation-${item.itemId}`}
                      style={{ fontSize: "0.85em", opacity: 0.8 }}
                    >
                      {" "}
                      {variationStatusLabel(item.variation.status)}
                    </span>
                  )}
                  {failedItemIds.has(item.itemId) && (
                    <span style={{ fontSize: "0.85em", opacity: 0.8 }}>
                      {" "}
                      (save failed)
                    </span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        </div>

        <div>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: "12px",
            }}
          >
            <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
              <span data-testid="bulk-review-status">
                {statusLabel(selectedItem.status)}
              </span>
              {variation && (
                <span
                  data-testid="bulk-review-variation-status"
                  style={{ fontSize: "0.85em" }}
                >
                  {variationStatusLabel(variation.status)}
                </span>
              )}
              {hasSaveFailed && (
                <span
                  data-testid="bulk-review-save-status"
                  style={{ color: "var(--color-error, #dc2626)", fontSize: "0.85em" }}
                >
                  Save failed, retrying...
                </span>
              )}
              {generateHint && (
                <span
                  data-testid="bulk-review-generate-hint"
                  style={{ fontSize: "0.85em", opacity: 0.8 }}
                >
                  {generateHint}
                </span>
              )}
              {generateError && (
                <span
                  data-testid="bulk-review-generate-error"
                  style={{ color: "var(--color-error, #dc2626)", fontSize: "0.85em" }}
                >
                  Generate failed: {generateError}
                </span>
              )}
              {revalidateError && (
                <span
                  data-testid="bulk-review-revalidate-error"
                  style={{ color: "var(--color-error, #dc2626)", fontSize: "0.85em" }}
                >
                  Revalidate failed: {revalidateError}
                </span>
              )}
              {attestError && (
                <span
                  data-testid="bulk-review-attest-error"
                  style={{ color: "var(--color-error, #dc2626)", fontSize: "0.85em" }}
                >
                  Attest failed: {attestError}
                </span>
              )}
            </div>
            <div style={{ display: "flex", gap: "8px" }}>
              {isEditable && batch.ingestionMode !== "original" && (
                <button
                  type="button"
                  data-testid="bulk-review-generate"
                  onClick={() => handleGenerate(selectedItem)}
                  disabled={generateDisabledReason !== ""}
                  title={generateHint || undefined}
                >
                  {variation?.status === "failed"
                    ? "Generate Again"
                    : "Generate variant"}
                </button>
              )}
              {variation?.status === "needs-validation" && (
                <button
                  type="button"
                  data-testid="bulk-review-revalidate"
                  onClick={() => handleRevalidate(selectedItem)}
                  disabled={revalidateDisabledReason !== "" || revalidating}
                  title={revalidateDisabledReason || undefined}
                >
                  {revalidating ? "Revalidating..." : "Revalidate"}
                </button>
              )}
              {stalePassReport && (
                <button
                  type="button"
                  data-testid="bulk-review-attest"
                  onClick={() => handleAttest(selectedItem)}
                  disabled={revalidateDisabledReason !== "" || attesting}
                  title={
                    revalidateDisabledReason ||
                    "Keep the existing validation without revalidating"
                  }
                >
                  {attesting ? "Keeping..." : "Keep validation"}
                </button>
              )}
              {selectedItem.status === "failed" && (
                <button
                  type="button"
                  data-testid="bulk-review-retry"
                  onClick={() => onRetry(selectedItem.itemId)}
                  disabled={isActionWorking}
                >
                  Retry extraction
                </button>
              )}
              {isDeleted ? (
                <button
                  type="button"
                  data-testid="bulk-review-undo"
                  onClick={() => onUndoDelete(selectedItem.itemId)}
                  disabled={isActionWorking}
                >
                  Undo delete
                </button>
              ) : (
                <button
                  type="button"
                  data-testid="bulk-review-delete"
                  onClick={() => onDelete(selectedItem.itemId)}
                  disabled={isActionWorking}
                >
                  Delete
                </button>
              )}
            </div>
          </div>

          {selectedItem.extraction.failureMessage && (
            <div
              data-testid="bulk-review-failure"
              style={{ color: "var(--color-error, #dc2626)", marginBottom: "12px" }}
            >
              {selectedItem.extraction.failureMessage}
            </div>
          )}

          {previewUrl && (
            <img
              src={previewUrl}
              alt="Crop preview"
              data-testid="bulk-review-preview"
              style={{
                maxWidth: "100%",
                maxHeight: "200px",
                marginBottom: "12px",
                border: "1px solid var(--color-border)",
              }}
            />
          )}

          {selectedItem.variation?.candidate && (
            <div
              data-testid="bulk-review-edit-target"
              role="group"
              aria-label="Editing target"
              style={{ display: "flex", gap: "8px", marginBottom: "12px" }}
            >
              <button
                type="button"
                data-testid="bulk-review-edit-candidate"
                aria-pressed={activeTarget === "candidate"}
                onClick={() => setEditTarget("candidate")}
                disabled={isFieldDisabled}
              >
                Edit candidate
              </button>
              <button
                type="button"
                data-testid="bulk-review-edit-source"
                aria-pressed={activeTarget === "source"}
                onClick={() => setEditTarget("source")}
                disabled={isFieldDisabled}
              >
                Edit source
              </button>
            </div>
          )}

          {activeTarget === "source" && variation?.original && (
            <div
              data-testid="bulk-review-source-invalidation-warning"
              style={{
                color: "var(--color-warning, #b45309)",
                fontSize: "0.9em",
                marginBottom: "12px",
              }}
            >
              Saving source changes invalidates the current variant and
              requires a new generation.
            </div>
          )}

          {stalePassReport && (
            <div
              data-testid="bulk-review-stale-validation"
              style={{
                color: "var(--color-warning, #b45309)",
                fontSize: "0.9em",
                marginBottom: "12px",
              }}
            >
              Validation covers a previous version of this candidate.
              Revalidate, or keep it if you accept the current version as-is.
            </div>
          )}
          {(variation?.status === "failed" ||
            stalePassReport ||
            (variation?.status === "ready" && variation?.attestation)) && (
            <VariationFailureEvidence variation={variation} />
          )}

          {/* #658: fail-override attestation sits below the evidence panel
              so the failing checks the teacher is waving stay visible. */}
          {failOverrideEligible && variation && (
            <div style={{ marginBottom: "12px" }}>
              <button
                type="button"
                data-testid="bulk-review-attest-fail"
                onClick={() => handleAttest(selectedItem)}
                disabled={revalidateDisabledReason !== "" || attesting}
                title={
                  revalidateDisabledReason ||
                  "Accept the failed judgment checks and approve this variant"
                }
              >
                {attesting
                  ? "Approving..."
                  : "Override failed checks — approve anyway"}
              </button>
            </div>
          )}

          <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
            <label>
              Text
              <textarea
                data-testid="bulk-review-text"
                value={currentDraft.text ?? ""}
                onChange={(event) =>
                  updateDraft(activeKey, { text: event.target.value })
                }
                disabled={isFieldDisabled}
                rows={4}
                style={{
                  width: "100%",
                  border: selectedRequiredFieldGaps.text
                    ? ACTION_REQUIRED_BORDER
                    : undefined,
                }}
              />
            </label>

            <div>
              <div
                style={{
                  fontSize: "0.85em",
                  fontWeight: 600,
                  marginBottom: "6px",
                }}
              >
                Text preview
              </div>
              <div
                data-testid="bulk-review-text-preview"
                style={{
                  border: "1px solid var(--color-border)",
                  borderRadius: "6px",
                  padding: "12px",
                  minHeight: "64px",
                  backgroundColor: "var(--color-surface-muted)",
                }}
              >
                <LatexText
                  text={currentDraft.text ?? ""}
                  style={{ whiteSpace: "pre-wrap" }}
                />
              </div>
            </div>

            <div style={{ display: "flex", gap: "12px" }}>
              <label style={{ flex: 1 }}>
                Problem type
                <select
                  data-testid="bulk-review-type"
                  value={currentDraft.problemType ?? "short-answer"}
                  onChange={(event) =>
                    updateDraft(activeKey, {
                      problemType: event.target.value,
                    })
                  }
                  disabled={isFieldDisabled}
                  style={{
                    width: "100%",
                    border: selectedRequiredFieldGaps.problemType
                      ? ACTION_REQUIRED_BORDER
                      : undefined,
                  }}
                >
                  {PROBLEM_TYPES.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>

              <label style={{ flex: 1 }}>
                Subject
                <select
                  data-testid="bulk-review-subject"
                  value={currentDraft.subject ?? "math"}
                  onChange={(event) =>
                    updateDraft(activeKey, {
                      subject: event.target.value,
                    })
                  }
                  disabled={isFieldDisabled || activeTarget === "candidate"}
                  title={
                    activeTarget === "candidate"
                      ? "Candidates share the source subject"
                      : undefined
                  }
                  style={{ width: "100%" }}
                >
                  {SUBJECTS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            <label>
              Correct answer
              <input
                type="text"
                data-testid="bulk-review-answer"
                value={currentDraft.correctAnswer ?? ""}
                onChange={(event) =>
                  updateDraft(activeKey, {
                    correctAnswer: event.target.value,
                  })
                }
                disabled={isFieldDisabled}
                style={{
                  width: "100%",
                  border: selectedRequiredFieldGaps.correctAnswer
                    ? ACTION_REQUIRED_BORDER
                    : undefined,
                }}
              />
            </label>

            <label>
              Graph DSL
              <textarea
                data-testid="bulk-review-graphdsl"
                value={currentDraft.graphDsl ?? ""}
                onChange={(event) =>
                  updateDraft(activeKey, {
                    graphDsl: event.target.value,
                  })
                }
                disabled={isFieldDisabled}
                rows={10}
                style={{
                  width: "100%",
                  minHeight: "180px",
                  resize: "vertical",
                  fontFamily: "monospace",
                  fontSize: "0.9em",
                  lineHeight: 1.4,
                }}
              />
            </label>

            {currentDraft.graphDsl?.trim() && (
              <div>
                <div
                  style={{
                    fontSize: "0.85em",
                    fontWeight: 600,
                    marginBottom: "6px",
                  }}
                >
                  Graph preview
                </div>
                <GraphSandbox dsl={currentDraft.graphDsl} height={300} />
              </div>
            )}

            {visibleRecentTags.length > 0 && (
              <div
                data-testid="bulk-review-recent-tags"
                style={{
                  display: "flex",
                  flexWrap: "wrap",
                  gap: "6px",
                  alignItems: "center",
                  marginBottom: "8px",
                }}
              >
                <span
                  style={{
                    fontSize: "0.85em",
                    fontWeight: 600,
                    color: "var(--color-text-muted)",
                  }}
                >
                  Recent tags:
                </span>
                {visibleRecentTags.map((tag) => (
                  <button
                    key={tag}
                    type="button"
                    data-testid={`bulk-review-recent-tag-${tag}`}
                    disabled={isFieldDisabled}
                    onClick={() => handleRecentTagClick(tag)}
                    style={{
                      padding: "2px 8px",
                      border: "1px solid var(--color-border)",
                      borderRadius: "4px",
                      backgroundColor: "var(--color-surface)",
                      color: "var(--color-text)",
                      fontSize: "0.8em",
                      cursor: isFieldDisabled ? "not-allowed" : "pointer",
                    }}
                  >
                    {tag}
                  </button>
                ))}
              </div>
            )}

            <TagInput
              tags={currentDraft.tags ?? []}
              onChange={(tags) =>
                handleTagsChange(activeKey, tags, currentDraft.tags ?? [])
              }
              suggestions={reviewTagSuggestions}
              placeholder="Add a tag..."
              disabled={isFieldDisabled}
              label="Tags"
              testId="bulk-review-tags"
            />
          </div>
        </div>
      </div>

      <div style={{ marginTop: "16px", textAlign: "right" }}>
        <button
          type="button"
          data-testid="bulk-review-continue"
          onClick={onContinue}
          disabled={!canContinue || isLoading}
        >
          Continue to submit
        </button>
      </div>
    </div>
  );
}

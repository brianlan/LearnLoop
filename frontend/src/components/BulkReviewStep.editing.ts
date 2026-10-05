import { useCallback, useEffect, useRef, useState } from "react";
import type {
  BulkDraft,
  BulkItem,
  VariationOriginalPayload,
} from "@/types/bulkIngestion";
import { getRequiredFieldGaps, isVariantBusy } from "./BulkReviewStep.helpers";

// Source draft and variant candidate are separate editing targets with
// separate autosave buffers and save endpoints.
export type EditTarget = "source" | "candidate";

// Server-side identity of the content a buffer is based on. `revision` is the
// item contentRevision sent as expectedRevision; `generation` identifies the
// candidate produced by one generation run.
interface TargetStamp {
  revision: number;
  generation: number;
  updatedAt: number;
}

function bufferKey(itemId: string, target: EditTarget): string {
  return `${itemId}::${target}`;
}

function targetDraft(item: BulkItem, target: EditTarget): BulkDraft {
  if (target === "candidate" && item.variation?.candidate) {
    const candidate = item.variation.candidate;
    return {
      text: candidate.text ?? "",
      problemType: candidate.problemType ?? "short-answer",
      graphDsl: candidate.graphDsl ?? "",
      correctAnswer: candidate.correctAnswer ?? "",
      // Tags are shared metadata living in the item draft; the candidate
      // payload never carries them (seeding from the candidate would send an
      // empty tag list and clear the shared tags on every candidate save).
      tags: item.draft.tags ?? [],
      // Display-only: a candidate has no subject of its own and candidate
      // saves never send this field.
      subject: item.variation.original?.subject ?? item.draft.subject ?? "math",
    };
  }
  return {
    text: item.draft.text ?? "",
    problemType: item.draft.problemType ?? "short-answer",
    graphDsl: item.draft.graphDsl ?? "",
    correctAnswer: item.draft.correctAnswer ?? "",
    tags: item.draft.tags ?? [],
    subject: item.draft.subject ?? "math",
  };
}

function targetStamp(item: BulkItem, target: EditTarget): TargetStamp {
  return {
    revision: item.contentRevision,
    generation:
      target === "candidate" ? item.variation?.generationCount ?? 0 : 0,
    updatedAt: Date.parse(item.updatedAt) || 0,
  };
}

// An older snapshot (stale save or poll response) never replaces what this
// buffer has already seen.
// ponytail: two writes sharing one millisecond fall through to content
// comparison below; exact ordering needs a server sequence number.
function isStaleStamp(
  incoming: TargetStamp,
  current: TargetStamp | undefined,
): boolean {
  if (!current) return false;
  return (
    incoming.updatedAt < current.updatedAt ||
    incoming.revision < current.revision ||
    incoming.generation < current.generation
  );
}

// The reviewed source confirmed by Generate.
function sourcePayloadFromDraft(draft: BulkDraft): VariationOriginalPayload {
  return {
    text: draft.text ?? "",
    problemType: draft.problemType ?? "short-answer",
    correctAnswer: draft.correctAnswer ?? "",
    // Match the null value sent by draft saves for an empty graph.
    graphDsl: draft.graphDsl || null,
    subject: draft.subject ?? null,
  };
}

const TARGETS: EditTarget[] = ["source", "candidate"];

export interface ReviewEditingCallbacks {
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
}

export function useBulkReviewEditing(
  items: BulkItem[],
  { onUpdateDraft, onGenerate, onRevalidate, onAttest }: ReviewEditingCallbacks,
) {
  const [localDrafts, setLocalDrafts] = useState<Record<string, BulkDraft>>({});
  const [dirtyKeys, setDirtyKeys] = useState<Set<string>>(new Set());
  const [savingKeys, setSavingKeys] = useState<Set<string>>(new Set());
  const [saveFailures, setSaveFailures] = useState<Record<string, number>>({});
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
  const [generatingIds, setGeneratingIds] = useState<Set<string>>(new Set());
  const [generateErrors, setGenerateErrors] = useState<Record<string, string>>(
    {},
  );
  const pendingGenerateRef = useRef<Map<string, VariationOriginalPayload>>(
    new Map(),
  );
  const [revalidatingIds, setRevalidatingIds] = useState<Set<string>>(
    new Set(),
  );
  const [revalidateErrors, setRevalidateErrors] = useState<
    Record<string, string>
  >({});
  const [attestingIds, setAttestingIds] = useState<Set<string>>(new Set());
  const [attestErrors, setAttestErrors] = useState<Record<string, string>>({});

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

  const updateDraft = useCallback(
    (item: BulkItem, target: EditTarget, next: Partial<BulkDraft>) => {
      const key = bufferKey(item.itemId, target);
      const merged = {
        ...draftRefs.current,
        [key]: { ...(draftRefs.current[key] ?? targetDraft(item, target)), ...next },
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
        const serializedServerDraft = JSON.stringify(serverDraft);
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
      }, Math.min(500 * 2 ** failures, 4000));
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

  function actionStateFor(item: BulkItem, isEditable: boolean, isLoading: boolean) {
    const itemPrefix = item.itemId + "::";
    const sourceKey = bufferKey(item.itemId, "source");
    const candidateKey = bufferKey(item.itemId, "candidate");
    const itemSaving = [...savingKeys].some((key) => key.startsWith(itemPrefix));
    const hasSaveFailed = Object.keys(saveFailures).some((key) =>
      key.startsWith(itemPrefix),
    );
    const sourceGaps = getRequiredFieldGaps(getDraft(item, "source"));
    const sourceSaveFailed = saveFailures[sourceKey] !== undefined;
    const sourceSavePending = dirtyKeys.has(sourceKey) || savingKeys.has(sourceKey);
    const candidateSavePending =
      dirtyKeys.has(candidateKey) || savingKeys.has(candidateKey);
    const isActionWorking = isLoading || itemSaving;
    const generating = generatingIds.has(item.itemId);
    const revalidating = revalidatingIds.has(item.itemId);
    const attesting = attestingIds.has(item.itemId);

    let generateDisabledReason = "";
    if (!isEditable) {
      generateDisabledReason = "Item is not editable";
    } else if (sourceGaps.text || sourceGaps.problemType || sourceGaps.correctAnswer) {
      generateDisabledReason =
        "Source needs text, problem type and a confirmed answer";
    } else if (sourceSaveFailed) {
      generateDisabledReason = "Draft save failed, retrying";
    } else if (generating) {
      generateDisabledReason = "Generating...";
    } else if (item.variation && isVariantBusy(item.variation.status)) {
      generateDisabledReason = "Variant work is still running";
    }
    const generateHint =
      generateDisabledReason ||
      (sourceSavePending
        ? "Generate confirms the reviewed source once its save settles"
        : "");

    const revalidateDisabledReason = item.variation?.status !== "needs-validation"
      ? ""
      : !isEditable
        ? "Item is not editable"
        : isActionWorking
          ? "Draft save is still settling"
          : candidateSavePending
            ? "Candidate changes are still saving"
            : "";
    const attestDisabledReason = !isEditable
      ? "Item is not editable"
      : isActionWorking
        ? "Draft save is still settling"
        : candidateSavePending
          ? "Candidate changes are still saving"
          : "";

    return {
      isActionWorking,
      hasSaveFailed,
      generateDisabledReason,
      generateHint,
      generateError: generateErrors[item.itemId],
      revalidating,
      revalidateError: revalidateErrors[item.itemId],
      attesting,
      attestError: attestErrors[item.itemId],
      revalidateDisabledReason,
      attestDisabledReason,
    };
  }

  return {
    getDraft,
    updateDraft,
    handleGenerate,
    handleRevalidate,
    handleAttest,
    actionStateFor,
    failedItemIds: new Set(Object.keys(saveFailures).map((key) => key.split("::")[0])),
    hasPendingSaves: dirtyKeys.size > 0 || savingKeys.size > 0,
    hasSaveFailures: Object.keys(saveFailures).length > 0,
  };
}

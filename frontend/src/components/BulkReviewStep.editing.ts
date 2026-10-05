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
  updatedAtRaw: string;
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
    updatedAtRaw: item.updatedAt,
  };
}

// An older snapshot (stale save or poll response) never replaces what this
// buffer has already seen.
// ponytail: equal revisions and exact timestamps cannot be ordered;
// a server sequence number would resolve that case.
function isStaleStamp(
  incoming: TargetStamp,
  current: TargetStamp | undefined,
): boolean {
  if (!current) return false;
  return (
    incoming.updatedAt < current.updatedAt ||
    (incoming.updatedAt === current.updatedAt &&
      incoming.updatedAtRaw < current.updatedAtRaw) ||
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
  const [conflictKeys, setConflictKeys] = useState<Set<string>>(new Set());
  const draftRefs = useRef<Record<string, BulkDraft>>({});
  const dirtyRefs = useRef<Set<string>>(new Set());
  const saveFailuresRef = useRef<Record<string, number>>({});
  const conflictRefs = useRef<Set<string>>(new Set());
  const serverDraftRefs = useRef<Record<string, string>>({});
  // A save can resolve before its returned batch reaches props. Ignore the
  // previous prop snapshot until the acknowledged content arrives.
  const acknowledgedDraftRefs = useRef<Record<string, string>>({});
  const stampRefs = useRef<Record<string, TargetStamp>>({});
  // The revision used by a dirty full-form draft stays fixed until its own
  // save succeeds; observing another writer's revision cannot rebase it.
  const draftBaseRefs = useRef<Record<string, TargetStamp>>({});
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
  const generatingRefs = useRef<Set<string>>(new Set());
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

  const cancelPendingGenerate = useCallback((key: string) => {
    if (!pendingGenerateRef.current.delete(key)) return;
    generatingRefs.current.delete(key.split("::")[0]);
    setGeneratingIds((prev) => {
      const next = new Set(prev);
      next.delete(key.split("::")[0]);
      return next;
    });
  }, []);

  const markConflict = useCallback(
    (key: string) => {
      if (conflictRefs.current.has(key)) return;
      const next = new Set(conflictRefs.current).add(key);
      conflictRefs.current = next;
      setConflictKeys(next);
      cancelPendingGenerate(key);
    },
    [cancelPendingGenerate],
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
          generatingRefs.current.delete(itemId);
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
        generatingRefs.current.has(itemId) ||
        pendingGenerateRef.current.has(sourceKey)
      ) {
        return;
      }
      generatingRefs.current.add(itemId);
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
    [dirtyKeys, firePendingGenerate, getDraft, savingKeys],
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
      if (
        target === "source" &&
        generatingRefs.current.has(item.itemId) &&
        Object.keys(next).some((field) => field !== "tags")
      ) {
        return;
      }
      const key = bufferKey(item.itemId, target);
      if (!draftBaseRefs.current[key]) {
        draftBaseRefs.current[key] = stampRefs.current[key] ?? targetStamp(item, target);
      }
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
    const resetKeys = new Set<string>();
    const droppedKeys = new Set<string>();

    const hasTargetState = (key: string) =>
      draftRefs.current[key] !== undefined ||
      serverDraftRefs.current[key] !== undefined ||
      draftBaseRefs.current[key] !== undefined ||
      dirtyRefs.current.has(key) ||
      savingKeys.has(key) ||
      saveFailuresRef.current[key] !== undefined ||
      conflictRefs.current.has(key) ||
      inFlightRefs.current[key] !== undefined;

    for (const item of items) {
      for (const target of TARGETS) {
        const key = bufferKey(item.itemId, target);
        const incoming = targetStamp(item, target);
        const previousStamp = stampRefs.current[key];
        // A stale save/poll response never replaces newer state.
        if (isStaleStamp(incoming, previousStamp)) continue;

        const acknowledged = acknowledgedDraftRefs.current[key];
        if (acknowledged) {
          const incomingDraft =
            item.status === "deleted" ||
            (target === "candidate" && !item.variation?.candidate)
              ? undefined
              : JSON.stringify(targetDraft(item, target));
          if (incomingDraft === acknowledged) {
            delete acknowledgedDraftRefs.current[key];
          } else if (
            previousStamp &&
            incoming.revision === previousStamp.revision &&
            incoming.generation === previousStamp.generation &&
            incoming.updatedAt === previousStamp.updatedAt &&
            incoming.updatedAtRaw === previousStamp.updatedAtRaw
          ) {
            continue;
          } else {
            delete acknowledgedDraftRefs.current[key];
          }
        }
        stampRefs.current[key] = incoming;

        if (
          item.status === "deleted" ||
          (target === "candidate" && !item.variation?.candidate)
        ) {
          if (hasTargetState(key)) {
            resetKeys.add(key);
            droppedKeys.add(key);
          }
          continue;
        }

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
        if (generationChanged) resetKeys.add(key);

        const base = draftBaseRefs.current[key];
        if (
          !generationChanged &&
          base &&
          !inFlightRefs.current[key] &&
          (incoming.revision !== base.revision ||
            incoming.generation !== base.generation ||
            (previousServerDraft !== undefined &&
              previousServerDraft !== serializedServerDraft))
        ) {
          markConflict(key);
        }

        if (!generationChanged && previousServerDraft === serializedServerDraft) {
          continue;
        }
        if (
          !generationChanged &&
          (dirtyRefs.current.has(key) ||
            savingKeys.has(key) ||
            saveFailuresRef.current[key] !== undefined ||
            conflictRefs.current.has(key))
        ) {
          continue;
        }

        if (nextDrafts === undefined) {
          nextDrafts = { ...draftRefs.current };
        }
        nextDrafts[key] = serverDraft;
        if (!generationChanged) delete draftBaseRefs.current[key];
      }
    }

    if (resetKeys.size > 0) {
      for (const key of resetKeys) {
        inFlightRefs.current[key] = undefined;
        delete draftBaseRefs.current[key];
        delete acknowledgedDraftRefs.current[key];
        cancelPendingGenerate(key);
      }
      setDirtyKeys((prev) => {
        const next = new Set([...prev].filter((key) => !resetKeys.has(key)));
        dirtyRefs.current = next;
        return next;
      });
      setSavingKeys(
        (prev) => new Set([...prev].filter((key) => !resetKeys.has(key))),
      );
      setSaveFailures((prev) => {
        const next = { ...prev };
        for (const key of resetKeys) delete next[key];
        saveFailuresRef.current = next;
        return next;
      });
      setConflictKeys((prev) => {
        const next = new Set([...prev].filter((key) => !resetKeys.has(key)));
        conflictRefs.current = next;
        return next;
      });
    }

    if (droppedKeys.size > 0) {
      nextDrafts ??= { ...draftRefs.current };
      for (const key of droppedKeys) {
        delete nextDrafts[key];
        delete serverDraftRefs.current[key];
      }
    }

    if (nextDrafts !== undefined) {
      draftRefs.current = nextDrafts;
      setLocalDrafts(nextDrafts);
    }
  }, [items, savingKeys, cancelPendingGenerate, markConflict]);

  useEffect(() => {
    const timeoutIds: Record<string, number> = {};

    const finishSave = (
      key: string,
      seq: number,
      outcome: "success" | "failure",
      sentSerialized: string,
      result?: void | { contentRevision: number },
      error?: unknown,
    ) => {
      const sent = inFlightRefs.current[key];
      if (!sent || sent.seq !== seq) return;
      inFlightRefs.current[key] = undefined;
      setSavingKeys((prev) => {
        const next = new Set(prev);
        next.delete(key);
        return next;
      });

      const stamp = stampRefs.current[key];
      const savedRevision =
        result && typeof result === "object"
          ? result.contentRevision
          : sent.revision;
      // An observed version beyond this save's own result belongs to another
      // write. Keep the local draft, but do not silently rebase its full form.
      if (
        stamp &&
        (stamp.revision > savedRevision ||
          stamp.generation !== sent.generation)
      ) {
        markConflict(key);
        return;
      }

      if (outcome === "success") {
        const savedStamp = {
          revision: savedRevision,
          generation: sent.generation,
          updatedAt: stamp?.updatedAt ?? 0,
          updatedAtRaw: stamp?.updatedAtRaw ?? "",
        };
        draftBaseRefs.current[key] = savedStamp;
        stampRefs.current[key] = savedStamp;
        serverDraftRefs.current[key] = sentSerialized;
        acknowledgedDraftRefs.current[key] = sentSerialized;
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
            delete draftBaseRefs.current[key];
          }
          dirtyRefs.current = nextDirty;
          return nextDirty;
        });
        // The reviewed SOURCE save settled: confirm the queued Generate with
        // the post-save revision (falls back to the sent revision for saves
        // that do not bump contentRevision, e.g. tag-only edits). Candidate
        // saves pass their own key and never match the pending entry.
        firePendingGenerate(key, savedRevision);
      } else {
        cancelPendingGenerate(key);
        if (
          typeof error === "object" &&
          error !== null &&
          "code" in error &&
          error.code === "REVISION_MISMATCH"
        ) {
          markConflict(key);
          return;
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
        if (!dirtyRefs.current.has(key) || conflictRefs.current.has(key)) return;
        const draft = draftRefs.current[key];
        if (!draft) return;
        const sentDraft = JSON.parse(JSON.stringify(draft)) as BulkDraft;
        const sentSerialized = JSON.stringify(sentDraft);
        const base = draftBaseRefs.current[key] ?? stampRefs.current[key];
        const seq = (saveSeqRef.current += 1);
        inFlightRefs.current[key] = {
          seq,
          revision: base?.revision ?? 0,
          generation: base?.generation ?? 0,
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
            expectedRevision: base?.revision ?? 0,
          }),
        )
          .then((result) => finishSave(key, seq, "success", sentSerialized, result))
          .catch((error: unknown) =>
            finishSave(key, seq, "failure", sentSerialized, undefined, error),
          );
      }, Math.min(500 * 2 ** failures, 4000));
    };

    dirtyKeys.forEach((key) => {
      if (!savingKeys.has(key) && !conflictKeys.has(key)) {
        scheduleSave(key);
      }
    });

    return () => {
      Object.values(timeoutIds).forEach((id) => window.clearTimeout(id));
    };
  }, [
    dirtyKeys,
    savingKeys,
    conflictKeys,
    firePendingGenerate,
    cancelPendingGenerate,
    markConflict,
    onUpdateDraft,
  ]);

  function actionStateFor(item: BulkItem, isEditable: boolean, isLoading: boolean) {
    const itemPrefix = item.itemId + "::";
    const sourceKey = bufferKey(item.itemId, "source");
    const candidateKey = bufferKey(item.itemId, "candidate");
    const itemSaving = [...savingKeys].some((key) => key.startsWith(itemPrefix));
    const hasSaveFailed = Object.keys(saveFailures).some((key) =>
      key.startsWith(itemPrefix) && !conflictKeys.has(key),
    );
    const hasConflict = [...conflictKeys].some((key) => key.startsWith(itemPrefix));
    const sourceGaps = getRequiredFieldGaps(getDraft(item, "source"));
    const sourceSaveFailed = saveFailures[sourceKey] !== undefined;
    const sourceConflict = conflictKeys.has(sourceKey);
    const sourceSavePending = dirtyKeys.has(sourceKey) || savingKeys.has(sourceKey);
    const candidateSavePending =
      dirtyKeys.has(candidateKey) || savingKeys.has(candidateKey);
    const candidateConflict = conflictKeys.has(candidateKey);
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
    } else if (sourceConflict) {
      generateDisabledReason = "Source changed elsewhere; copy your edits and reload";
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
        : candidateConflict
          ? "Candidate changed elsewhere; copy your edits and reload"
          : isActionWorking
            ? "Draft save is still settling"
            : candidateSavePending
              ? "Candidate changes are still saving"
              : "";
    const attestDisabledReason = !isEditable
      ? "Item is not editable"
      : candidateConflict
        ? "Candidate changed elsewhere; copy your edits and reload"
        : isActionWorking
          ? "Draft save is still settling"
          : candidateSavePending
            ? "Candidate changes are still saving"
            : "";

    return {
      isActionWorking,
      hasSaveFailed,
      hasConflict,
      sourceLocked: generating,
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
    failedItemIds: new Set(
      Object.keys(saveFailures)
        .filter((key) => !conflictKeys.has(key))
        .map((key) => key.split("::")[0]),
    ),
    conflictedItemIds: new Set([...conflictKeys].map((key) => key.split("::")[0])),
    hasPendingSaves:
      [...dirtyKeys].some((key) => !conflictKeys.has(key)) || savingKeys.size > 0,
    hasSaveFailures: Object.keys(saveFailures).some((key) => !conflictKeys.has(key)),
    hasConflicts: conflictKeys.size > 0,
  };
}

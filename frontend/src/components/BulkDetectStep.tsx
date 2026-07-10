import { useCallback, useEffect, useRef, useState } from "react";
import type { BulkBatch, BulkImageBox } from "@/types/bulkIngestion";
import { BoxEditor } from "./BoxEditor";

export interface BulkDetectStepProps {
  batch: BulkBatch;
  isLoading: boolean;
  onDetect: (imageId: string) => void | Promise<void>;
  onSaveBoxes: (
    imageId: string,
    boxes: BulkImageBox[],
    subject?: string | null,
  ) => void | Promise<void>;
  onCommit: (imageId: string) => void | Promise<void>;
  onDelete: (imageId: string) => void | Promise<void>;
}

const SUBJECTS = [
  { value: "math", label: "Math" },
  { value: "english", label: "English" },
];

function statusLabel(status: string): string {
  switch (status) {
    case "uploaded":
      return "Ready to detect";
    case "detecting":
      return "Detecting boxes...";
    case "detect-failed":
      return "Detection failed";
    case "ready":
      return "Review boxes";
    default:
      return status;
  }
}

export function BulkDetectStep({
  batch,
  isLoading,
  onDetect,
  onSaveBoxes,
  onCommit,
  onDelete,
}: BulkDetectStepProps) {
  const [workingImageId, setWorkingImageId] = useState<string | null>(null);
  const [pendingBoxes, setPendingBoxes] = useState<Record<string, BulkImageBox[]>>({});
  const [pendingSubjects, setPendingSubjects] = useState<Record<string, string>>({});
  const shortcutSavingRef = useRef(false);

  const withImageLoader = useCallback(
    async (imageId: string, action: () => void | Promise<void>) => {
      setWorkingImageId(imageId);
      try {
        await action();
      } finally {
        setWorkingImageId(null);
      }
    },
    [],
  );

  const handleSave = useCallback(
    async (imageId: string, boxes: BulkImageBox[], subject: string) => {
      await onSaveBoxes(imageId, boxes, subject);
      setPendingBoxes((prev) => {
        const next = { ...prev };
        delete next[imageId];
        return next;
      });
      setPendingSubjects((prev) => {
        const next = { ...prev };
        delete next[imageId];
        return next;
      });
    },
    [onSaveBoxes],
  );

  const actionableImages = batch.images.filter(
    (image) => image.status !== "committed" && image.status !== "deleted",
  );

  const handleShortcutSave = useCallback(async () => {
    if (shortcutSavingRef.current) return;

    const pending: { imageId: string; boxes: BulkImageBox[]; subject: string }[] =
      [];
    for (const image of actionableImages) {
      const boxes = pendingBoxes[image.imageId] ?? image.boxes;
      const currentSubject = image.subject ?? "math";
      const subject = pendingSubjects[image.imageId] ?? currentSubject;
      if (
        JSON.stringify(boxes) !== JSON.stringify(image.boxes) ||
        subject !== currentSubject
      ) {
        pending.push({ imageId: image.imageId, boxes, subject });
      }
    }

    if (pending.length === 0) return;

    shortcutSavingRef.current = true;
    try {
      for (const card of pending) {
        await withImageLoader(card.imageId, () =>
          handleSave(card.imageId, card.boxes, card.subject),
        );
      }
    } finally {
      shortcutSavingRef.current = false;
    }
  }, [actionableImages, pendingBoxes, pendingSubjects, withImageLoader, handleSave]);

  useEffect(() => {
    function handleKeydown(event: KeyboardEvent) {
      if (event.key !== "s" || event.repeat) return;
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      const target = event.target as HTMLElement | null;
      if (
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.tagName === "SELECT" ||
          target.isContentEditable)
      ) {
        return;
      }
      event.preventDefault();
      void handleShortcutSave();
    }
    window.addEventListener("keydown", handleKeydown);
    return () => window.removeEventListener("keydown", handleKeydown);
  }, [handleShortcutSave]);

  return (
    <div data-testid="bulk-wizard-detect-step">
      <h2>Review detected boxes</h2>
      {actionableImages.length === 0 ? (
        <p data-testid="bulk-detect-empty">All images have been committed.</p>
      ) : (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: "24px",
          }}
        >
          {actionableImages.map((image) => {
            const boxes = pendingBoxes[image.imageId] ?? image.boxes;
            const currentSubject = image.subject ?? "math";
            const subject = pendingSubjects[image.imageId] ?? currentSubject;
            const isWorking = isLoading || workingImageId === image.imageId;
            const boxesChanged =
              JSON.stringify(boxes) !== JSON.stringify(image.boxes);
            const subjectChanged = subject !== currentSubject;
            const canDetect =
              image.status === "uploaded" || image.status === "detect-failed";
            const isReady = image.status === "ready";

            return (
              <div
                key={image.imageId}
                data-testid={`bulk-detect-image-${image.imageId}`}
                style={{
                  border: "1px solid var(--color-border)",
                  borderRadius: "8px",
                  padding: "16px",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    marginBottom: "12px",
                  }}
                >
                  <span data-testid={`bulk-detect-status-${image.imageId}`}>
                    {statusLabel(image.status)}
                  </span>
                  <div style={{ display: "flex", gap: "8px" }}>
                    <select
                      data-testid={`bulk-detect-subject-${image.imageId}`}
                      value={subject}
                      onChange={(event) =>
                        setPendingSubjects((prev) => ({
                          ...prev,
                          [image.imageId]: event.target.value,
                        }))
                      }
                      disabled={isWorking}
                    >
                      {SUBJECTS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                    {canDetect && (
                      <button
                        type="button"
                        data-testid={`bulk-detect-run-${image.imageId}`}
                        onClick={() =>
                          withImageLoader(image.imageId, () => onDetect(image.imageId))
                        }
                        disabled={isWorking}
                      >
                        Detect
                      </button>
                    )}
                    {isReady && (
                      <button
                        type="button"
                        data-testid={`bulk-detect-commit-${image.imageId}`}
                        onClick={() =>
                          withImageLoader(image.imageId, () => onCommit(image.imageId))
                        }
                        disabled={isWorking}
                      >
                        Commit
                      </button>
                    )}
                    <button
                      type="button"
                      data-testid={`bulk-detect-delete-${image.imageId}`}
                      onClick={() =>
                        withImageLoader(image.imageId, () => onDelete(image.imageId))
                      }
                      disabled={isWorking}
                    >
                      Delete
                    </button>
                  </div>
                </div>

                {image.detection.failureMessage && (
                  <div
                    data-testid={`bulk-detect-failure-${image.imageId}`}
                    style={{ color: "var(--color-error, #dc2626)", marginBottom: "12px" }}
                  >
                    {image.detection.failureMessage}
                  </div>
                )}

                <BoxEditor
                  imageUrl={image.sourceImage.mediaUrl || ""}
                  naturalWidth={image.sourceImage.width || 1}
                  naturalHeight={image.sourceImage.height || 1}
                  boxes={boxes}
                  onChange={(next) =>
                    setPendingBoxes((prev) => ({
                      ...prev,
                      [image.imageId]: next,
                    }))
                  }
                  readOnly={isWorking}
                />

                {(boxesChanged || subjectChanged) && (
                  <div style={{ marginTop: "12px" }}>
                    <button
                      type="button"
                      data-testid={`bulk-detect-save-${image.imageId}`}
                      onClick={() =>
                        withImageLoader(image.imageId, () =>
                          handleSave(image.imageId, boxes, subject),
                        )
                      }
                      disabled={isWorking}
                    >
                      Save
                    </button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

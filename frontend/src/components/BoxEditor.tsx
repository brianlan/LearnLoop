import { useCallback, useEffect, useRef, useState } from "react";
import type { BulkImageBox } from "@/types/bulkIngestion";
import {
  createBox,
  naturalBoxToRender,
  type Point,
  clampBox,
} from "@/utils/boxGeometry";

export interface BoxEditorProps {
  imageUrl: string;
  naturalWidth: number;
  naturalHeight: number;
  boxes: BulkImageBox[];
  onChange: (boxes: BulkImageBox[]) => void;
  readOnly?: boolean;
}

type InteractionMode =
  | { type: "idle" }
  | { type: "creating"; start: Point }
  | { type: "moving"; boxId: string; offset: Point }
  | { type: "resizing"; boxId: string; handle: ResizeHandle; start: Point };

type ResizeHandle = "nw" | "ne" | "sw" | "se";

const HANDLE_SIZE = 8;
const BOX_BORDER_WIDTH = 2;

export function BoxEditor({
  imageUrl,
  naturalWidth,
  naturalHeight,
  boxes,
  onChange,
  readOnly = false,
}: BoxEditorProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [renderedSize, setRenderedSize] = useState({ width: 0, height: 0 });
  const [selectedBoxId, setSelectedBoxId] = useState<string | null>(null);
  const [mode, setMode] = useState<InteractionMode>({ type: "idle" });
  const [previewBox, setPreviewBox] = useState<BulkImageBox | null>(null);

  const dims = {
    naturalWidth,
    naturalHeight,
    renderedWidth: renderedSize.width,
    renderedHeight: renderedSize.height,
  };

  const refreshSize = useCallback(() => {
    const container = containerRef.current;
    if (!container) return;
    const rect = container.getBoundingClientRect();
    setRenderedSize({ width: rect.width, height: rect.height });
  }, []);

  useEffect(() => {
    refreshSize();
    window.addEventListener("resize", refreshSize);
    return () => window.removeEventListener("resize", refreshSize);
  }, [refreshSize]);

  const getEventPoint = useCallback(
    (event: { clientX: number; clientY: number }): { point: Point; natural: Point } => {
      const container = containerRef.current;
      if (!container) {
        return { point: { x: 0, y: 0 }, natural: { x: 0, y: 0 } };
      }
      const rect = container.getBoundingClientRect();
      const point = {
        x: event.clientX - rect.left,
        y: event.clientY - rect.top,
      };
      return {
        point,
        natural: {
          x: (point.x / rect.width) * naturalWidth,
          y: (point.y / rect.height) * naturalHeight,
        },
      };
    },
    [naturalWidth, naturalHeight],
  );

  const handleBoxChange = useCallback(
    (nextBoxes: BulkImageBox[]) => {
      onChange(nextBoxes.map((box) => clampBox(box, naturalWidth, naturalHeight)));
    },
    [onChange, naturalWidth, naturalHeight],
  );

  const findBoxAt = useCallback(
    (point: Point): BulkImageBox | null => {
      for (let i = boxes.length - 1; i >= 0; i--) {
        const rendered = naturalBoxToRender(boxes[i], dims);
        if (
          point.x >= rendered.x - BOX_BORDER_WIDTH &&
          point.x <= rendered.x + rendered.width + BOX_BORDER_WIDTH &&
          point.y >= rendered.y - BOX_BORDER_WIDTH &&
          point.y <= rendered.y + rendered.height + BOX_BORDER_WIDTH
        ) {
          return boxes[i];
        }
      }
      return null;
    },
    [boxes, dims],
  );

  const findResizeHandle = useCallback(
    (box: BulkImageBox, point: Point): ResizeHandle | null => {
      const rendered = naturalBoxToRender(box, dims);
      const handles: { key: ResizeHandle; x: number; y: number }[] = [
        { key: "nw", x: rendered.x, y: rendered.y },
        { key: "ne", x: rendered.x + rendered.width, y: rendered.y },
        { key: "sw", x: rendered.x, y: rendered.y + rendered.height },
        { key: "se", x: rendered.x + rendered.width, y: rendered.y + rendered.height },
      ];
      for (const handle of handles) {
        if (
          Math.abs(point.x - handle.x) <= HANDLE_SIZE &&
          Math.abs(point.y - handle.y) <= HANDLE_SIZE
        ) {
          return handle.key;
        }
      }
      return null;
    },
    [dims],
  );

  const handleMouseDown = useCallback(
    (event: React.MouseEvent) => {
      if (readOnly || renderedSize.width === 0) return;
      event.preventDefault();
      const { point, natural } = getEventPoint(event);

      const targetBox = findBoxAt(point);
      if (targetBox) {
        setSelectedBoxId(targetBox.boxId);
        const handle = findResizeHandle(targetBox, point);
        if (handle) {
          setMode({ type: "resizing", boxId: targetBox.boxId, handle, start: natural });
        } else {
          setMode({
            type: "moving",
            boxId: targetBox.boxId,
            offset: {
              x: natural.x - targetBox.x,
              y: natural.y - targetBox.y,
            },
          });
        }
        return;
      }

      setSelectedBoxId(null);
      setMode({ type: "creating", start: natural });
    },
    [readOnly, renderedSize, getEventPoint, findBoxAt, findResizeHandle],
  );

  const handleMouseMove = useCallback(
    (event: React.MouseEvent) => {
      if (mode.type === "idle" || renderedSize.width === 0) return;
      event.preventDefault();
      const { natural } = getEventPoint(event);

      if (mode.type === "creating") {
        setPreviewBox(createBox(mode.start, natural, naturalWidth, naturalHeight));
        return;
      }

      if (mode.type === "moving") {
        setPreviewBox(
          clampBox(
            {
              ...boxes.find((b) => b.boxId === mode.boxId)!,
              x: natural.x - mode.offset.x,
              y: natural.y - mode.offset.y,
            },
            naturalWidth,
            naturalHeight,
          ),
        );
        return;
      }

      if (mode.type === "resizing") {
        const box = boxes.find((b) => b.boxId === mode.boxId)!;
        let nextBox = { ...box };
        switch (mode.handle) {
          case "nw":
            nextBox = {
              ...box,
              x: Math.min(natural.x, box.x + box.width),
              y: Math.min(natural.y, box.y + box.height),
              width: Math.abs(box.x + box.width - natural.x),
              height: Math.abs(box.y + box.height - natural.y),
            };
            break;
          case "ne":
            nextBox = {
              ...box,
              y: Math.min(natural.y, box.y + box.height),
              width: Math.max(0, natural.x - box.x),
              height: Math.abs(box.y + box.height - natural.y),
            };
            break;
          case "sw":
            nextBox = {
              ...box,
              x: Math.min(natural.x, box.x + box.width),
              width: Math.abs(box.x + box.width - natural.x),
              height: Math.max(0, natural.y - box.y),
            };
            break;
          case "se":
            nextBox = {
              ...box,
              width: Math.max(0, natural.x - box.x),
              height: Math.max(0, natural.y - box.y),
            };
            break;
        }
        setPreviewBox(clampBox(nextBox, naturalWidth, naturalHeight));
      }
    },
    [mode, getEventPoint, naturalWidth, naturalHeight, boxes, renderedSize],
  );

  const handleMouseUp = useCallback(() => {
    if (mode.type === "idle") return;

    if (mode.type === "creating" && previewBox) {
      if (previewBox.width > 0 && previewBox.height > 0) {
        const newBox = clampBox(previewBox, naturalWidth, naturalHeight);
        handleBoxChange([...boxes, newBox]);
        setSelectedBoxId(newBox.boxId);
      }
    } else if ((mode.type === "moving" || mode.type === "resizing") && previewBox) {
      handleBoxChange(
        boxes.map((box) => (box.boxId === previewBox.boxId ? previewBox : box)),
      );
    }

    setMode({ type: "idle" });
    setPreviewBox(null);
  }, [mode, previewBox, boxes, handleBoxChange, naturalWidth, naturalHeight]);

  const handleDelete = useCallback(
    (boxId: string) => {
      handleBoxChange(boxes.filter((box) => box.boxId !== boxId));
      if (selectedBoxId === boxId) setSelectedBoxId(null);
    },
    [boxes, handleBoxChange, selectedBoxId],
  );

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Delete" || event.key === "Backspace") {
        if (selectedBoxId) handleDelete(selectedBoxId);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [selectedBoxId, handleDelete]);

  const displayBoxes = previewBox
    ? mode.type === "creating"
      ? [...boxes, previewBox]
      : boxes.map((box) => (box.boxId === previewBox.boxId ? previewBox : box))
    : boxes;

  return (
    <div
      ref={containerRef}
      data-testid="box-editor"
      style={{
        position: "relative",
        display: "inline-block",
        maxWidth: "100%",
        userSelect: "none",
      }}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseLeave={handleMouseUp}
    >
      <img
        src={imageUrl}
        alt="Review"
        data-testid="box-editor-image"
        style={{
          display: "block",
          maxWidth: "100%",
          height: "auto",
          pointerEvents: "none",
        }}
      />
      {displayBoxes.map((box) => {
        const rendered = naturalBoxToRender(box, dims);
        const isSelected = selectedBoxId === box.boxId;
        return (
          <div key={box.boxId}>
            <div
              data-testid={`box-${box.boxId}`}
              style={{
                position: "absolute",
                left: rendered.x,
                top: rendered.y,
                width: rendered.width,
                height: rendered.height,
                border: `${BOX_BORDER_WIDTH}px solid ${isSelected ? "var(--color-primary, #2563eb)" : "var(--color-success, #16a34a)"}`,
                backgroundColor: isSelected
                  ? "rgba(37, 99, 235, 0.1)"
                  : "rgba(22, 163, 74, 0.1)",
                cursor: readOnly ? "default" : "move",
              }}
            />
            {isSelected && !readOnly && (
              <>
                {(["nw", "ne", "sw", "se"] as ResizeHandle[]).map((handle) => {
                  const left =
                    handle === "nw" || handle === "sw"
                      ? rendered.x - HANDLE_SIZE / 2
                      : rendered.x + rendered.width - HANDLE_SIZE / 2;
                  const top =
                    handle === "nw" || handle === "ne"
                      ? rendered.y - HANDLE_SIZE / 2
                      : rendered.y + rendered.height - HANDLE_SIZE / 2;
                  return (
                    <div
                      key={handle}
                      data-testid={`handle-${handle}-${box.boxId}`}
                      style={{
                        position: "absolute",
                        left,
                        top,
                        width: HANDLE_SIZE,
                        height: HANDLE_SIZE,
                        backgroundColor: "var(--color-primary, #2563eb)",
                        borderRadius: "50%",
                        cursor:
                          handle === "nw" || handle === "se"
                            ? "nwse-resize"
                            : "nesw-resize",
                      }}
                    />
                  );
                })}
                <button
                  type="button"
                  data-testid={`delete-box-${box.boxId}`}
                  onClick={(event) => {
                    event.stopPropagation();
                    handleDelete(box.boxId);
                  }}
                  style={{
                    position: "absolute",
                    left: rendered.x + rendered.width - 12,
                    top: rendered.y - 12,
                    width: 20,
                    height: 20,
                    borderRadius: "50%",
                    border: "none",
                    backgroundColor: "var(--color-error, #dc2626)",
                    color: "white",
                    cursor: "pointer",
                    fontSize: 12,
                    lineHeight: "20px",
                    padding: 0,
                  }}
                >
                  ×
                </button>
              </>
            )}
          </div>
        );
      })}
    </div>
  );
}

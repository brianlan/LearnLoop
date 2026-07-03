import type { BulkImageBox } from "@/types/bulkIngestion";

export interface Point {
  x: number;
  y: number;
}

export interface Size {
  width: number;
  height: number;
}

export interface RenderedImageDimensions {
  naturalWidth: number;
  naturalHeight: number;
  renderedWidth: number;
  renderedHeight: number;
}

export function naturalPointToRender(
  point: Point,
  dims: RenderedImageDimensions,
): Point {
  const scaleX = dims.renderedWidth / dims.naturalWidth;
  const scaleY = dims.renderedHeight / dims.naturalHeight;
  return {
    x: point.x * scaleX,
    y: point.y * scaleY,
  };
}

export function naturalBoxToRender(
  box: BulkImageBox,
  dims: RenderedImageDimensions,
): BulkImageBox {
  const topLeft = naturalPointToRender({ x: box.x, y: box.y }, dims);
  const bottomRight = naturalPointToRender(
    { x: box.x + box.width, y: box.y + box.height },
    dims,
  );
  return {
    ...box,
    x: topLeft.x,
    y: topLeft.y,
    width: bottomRight.x - topLeft.x,
    height: bottomRight.y - topLeft.y,
  };
}

const MIN_BOX_SIZE = 4;

export function clampBox(
  box: BulkImageBox,
  naturalWidth: number,
  naturalHeight: number,
): BulkImageBox {
  let x = Math.max(0, Math.min(box.x, naturalWidth - MIN_BOX_SIZE));
  let y = Math.max(0, Math.min(box.y, naturalHeight - MIN_BOX_SIZE));
  let width = Math.max(MIN_BOX_SIZE, box.width);
  let height = Math.max(MIN_BOX_SIZE, box.height);

  if (x + width > naturalWidth) {
    width = naturalWidth - x;
  }
  if (y + height > naturalHeight) {
    height = naturalHeight - y;
  }

  return { ...box, x, y, width, height };
}

export function createBox(
  naturalStart: Point,
  naturalEnd: Point,
  naturalWidth: number,
  naturalHeight: number,
): BulkImageBox {
  const raw: BulkImageBox = {
    boxId: generateBoxId(),
    x: Math.min(naturalStart.x, naturalEnd.x),
    y: Math.min(naturalStart.y, naturalEnd.y),
    width: Math.abs(naturalEnd.x - naturalStart.x),
    height: Math.abs(naturalEnd.y - naturalStart.y),
  };
  return clampBox(raw, naturalWidth, naturalHeight);
}

let boxIdCounter = 0;

export function generateBoxId(): string {
  boxIdCounter += 1;
  return `client-box-${boxIdCounter}`;
}

export function resetBoxIdCounter(): void {
  boxIdCounter = 0;
}

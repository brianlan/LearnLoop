import { describe, expect, it, beforeEach } from "vitest";
import type { BulkImageBox } from "@/types/bulkIngestion";
import {
  clampBox,
  createBox,
  expandBoxWithMargins,
  naturalBoxToRender,
  naturalPointToRender,
  resetBoxIdCounter,
} from "./boxGeometry";

const dims = {
  naturalWidth: 200,
  naturalHeight: 100,
  renderedWidth: 100,
  renderedHeight: 50,
};

describe("boxGeometry", () => {
  beforeEach(() => {
    resetBoxIdCounter();
  });

  it("converts a natural point to rendered coordinates", () => {
    expect(naturalPointToRender({ x: 100, y: 50 }, dims)).toEqual({
      x: 50,
      y: 25,
    });
  });

  it("converts a natural box to rendered coordinates", () => {
    const box: BulkImageBox = {
      boxId: "a",
      x: 40,
      y: 20,
      width: 60,
      height: 30,
    };
    expect(naturalBoxToRender(box, dims)).toEqual({
      boxId: "a",
      x: 20,
      y: 10,
      width: 30,
      height: 15,
    });
  });

  it("clamps boxes inside image bounds and enforces a minimum size", () => {
    const box: BulkImageBox = {
      boxId: "a",
      x: 190,
      y: 90,
      width: 20,
      height: 20,
    };
    expect(clampBox(box, 200, 100)).toEqual({
      boxId: "a",
      x: 190,
      y: 90,
      width: 10,
      height: 10,
    });
  });

  it("creates a box from two natural points", () => {
    const box = createBox({ x: 100, y: 80 }, { x: 50, y: 40 }, 200, 100);
    expect(box).toMatchObject({
      boxId: "client-box-1",
      x: 50,
      y: 40,
      width: 50,
      height: 40,
    });
  });

  describe("expandBoxWithMargins", () => {
    it("expands a centered box by 5% horizontal and 2% vertical margin per side", () => {
      const box: BulkImageBox = {
        boxId: "box-1",
        x: 100,
        y: 50,
        width: 100,
        height: 50,
      };
      expect(expandBoxWithMargins(box, 400, 200)).toEqual({
        boxId: "box-1",
        x: 80,
        y: 46,
        width: 140,
        height: 58,
      });
    });

    it("clamps expansion to image bounds on every side", () => {
      const box: BulkImageBox = {
        boxId: "box-edge",
        x: 4,
        y: 1,
        width: 92,
        height: 49,
      };
      expect(expandBoxWithMargins(box, 100, 50)).toEqual({
        boxId: "box-edge",
        x: 0,
        y: 0,
        width: 100,
        height: 50,
      });
    });

    it("preserves extra box properties", () => {
      const box: BulkImageBox = {
        boxId: "box-extra",
        x: 50,
        y: 50,
        width: 20,
        height: 20,
        page: 2,
        label: "problem",
      };
      const expanded = expandBoxWithMargins(box, 200, 200);
      expect(expanded.boxId).toBe("box-extra");
      expect(expanded.page).toBe(2);
      expect(expanded.label).toBe("problem");
    });

    it("returns the same box when image dimensions are missing or invalid", () => {
      const box: BulkImageBox = {
        boxId: "box-missing",
        x: 10,
        y: 10,
        width: 20,
        height: 20,
      };
      expect(expandBoxWithMargins(box, 0, 100)).toEqual(box);
      expect(expandBoxWithMargins(box, 100, 0)).toEqual(box);
      expect(expandBoxWithMargins(box, -10, 100)).toEqual(box);
    });
  });
});

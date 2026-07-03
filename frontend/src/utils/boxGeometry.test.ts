import { describe, expect, it, beforeEach } from "vitest";
import type { BulkImageBox } from "@/types/bulkIngestion";
import {
  clampBox,
  createBox,
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
});

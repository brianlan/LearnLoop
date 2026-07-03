import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { BoxEditor } from "./BoxEditor";
import type { BulkImageBox } from "@/types/bulkIngestion";
import { resetBoxIdCounter } from "@/utils/boxGeometry";

const NATURAL_W = 200;
const NATURAL_H = 100;

function renderEditor(
  boxes: BulkImageBox[],
  onChange: (boxes: BulkImageBox[]) => void = vi.fn(),
) {
  const { container } = render(
    <BoxEditor
      imageUrl="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
      naturalWidth={NATURAL_W}
      naturalHeight={NATURAL_H}
      boxes={boxes}
      onChange={onChange}
    />,
  );
  const editor = screen.getByTestId("box-editor");
  vi.spyOn(editor, "getBoundingClientRect").mockReturnValue({
    x: 0,
    y: 0,
    width: 200,
    height: 100,
    top: 0,
    left: 0,
    right: 200,
    bottom: 100,
    toJSON: () => "",
  });
  fireEvent.resize(window);
  return { container, editor };
}

describe("BoxEditor", () => {
  beforeEach(() => {
    resetBoxIdCounter();
  });

  it("renders the image and a box overlay", () => {
    renderEditor([{ boxId: "b1", x: 10, y: 10, width: 50, height: 30 }]);
    expect(screen.getByTestId("box-editor-image")).toBeInTheDocument();
    expect(screen.getByTestId("box-b1")).toBeInTheDocument();
  });

  it("creates a new box by dragging on the image", () => {
    const onChange = vi.fn();
    renderEditor([], onChange);
    const editor = screen.getByTestId("box-editor");

    fireEvent.mouseDown(editor, { clientX: 20, clientY: 20 });
    fireEvent.mouseMove(editor, { clientX: 80, clientY: 60 });
    fireEvent.mouseUp(editor);

    expect(onChange).toHaveBeenCalledTimes(1);
    const boxes = onChange.mock.calls[0][0];
    expect(boxes).toHaveLength(1);
    expect(boxes[0].x).toBe(20);
    expect(boxes[0].y).toBe(20);
    expect(boxes[0].width).toBe(60);
    expect(boxes[0].height).toBe(40);
  });

  it("clamps created boxes inside image bounds", () => {
    const onChange = vi.fn();
    renderEditor([], onChange);
    const editor = screen.getByTestId("box-editor");

    fireEvent.mouseDown(editor, { clientX: 180, clientY: 80 });
    fireEvent.mouseMove(editor, { clientX: 300, clientY: 150 });
    fireEvent.mouseUp(editor);

    const box = onChange.mock.calls[0][0][0];
    expect(box.x + box.width).toBeLessThanOrEqual(NATURAL_W);
    expect(box.y + box.height).toBeLessThanOrEqual(NATURAL_H);
  });

  it("does not call onChange during mousemove", () => {
    const onChange = vi.fn();
    renderEditor([], onChange);
    const editor = screen.getByTestId("box-editor");

    fireEvent.mouseDown(editor, { clientX: 10, clientY: 10 });
    fireEvent.mouseMove(editor, { clientX: 20, clientY: 20 });
    fireEvent.mouseMove(editor, { clientX: 30, clientY: 30 });
    fireEvent.mouseMove(editor, { clientX: 40, clientY: 40 });
    fireEvent.mouseUp(editor);

    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it("deletes a selected box when clicking the delete button", () => {
    const onChange = vi.fn();
    const { editor } = renderEditor([{ boxId: "b1", x: 10, y: 10, width: 50, height: 30 }], onChange);

    fireEvent.mouseDown(editor, { clientX: 15, clientY: 15 });
    fireEvent.mouseUp(editor);
    fireEvent.click(screen.getByTestId("delete-box-b1"));

    expect(onChange).toHaveBeenCalledWith([]);
  });

  it("selects a box on click and shows handles", () => {
    const { editor } = renderEditor([{ boxId: "b1", x: 10, y: 10, width: 50, height: 30 }]);

    fireEvent.mouseDown(editor, { clientX: 15, clientY: 15 });
    fireEvent.mouseUp(editor);

    expect(screen.getByTestId("handle-nw-b1")).toBeInTheDocument();
    expect(screen.getByTestId("handle-se-b1")).toBeInTheDocument();
  });
});

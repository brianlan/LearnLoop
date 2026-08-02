import { describe, it, expect, vi } from "vitest";
import { render, fireEvent } from "@testing-library/react";
import { AnswerInput, parseOptions, extractOptionKey } from "./AnswerInput";

describe("parseOptions", () => {
  it("returns lettered choices when present", () => {
    const text = "1. — Could I use your bike?\n— Yes, you ________. But you ________ keep it clean.\nA. could; can\nB. can; must\nC. must; can\nD. could; must";
    const result = parseOptions(text);
    expect(result).toHaveLength(4);
    expect(result[0]).toMatch(/^A/);
    expect(result[1]).toMatch(/^B/);
    expect(result[2]).toMatch(/^C/);
    expect(result[3]).toMatch(/^D/);
  });

  it("ignores numeric question stems when lettered choices exist", () => {
    const text = "1. What is 2+2?\n2. What is 3+3?\nA. 4\nB. 5\nC. 6\nD. 7";
    const result = parseOptions(text);
    expect(result).toHaveLength(4);
    expect(result.every((r) => /^[A-Z]/.test(r))).toBe(true);
  });

  it("returns numeric choices when no lettered choices exist", () => {
    const text = "Choose the correct answer:\n1. First option\n2. Second option\n3. Third option";
    const result = parseOptions(text);
    expect(result).toHaveLength(3);
    expect(result[0]).toMatch(/^1/);
  });

  it("returns empty array when no options are found", () => {
    const text = "This is just plain text\nwith no option markers\nat all.";
    const result = parseOptions(text);
    expect(result).toEqual([]);
  });

  it("handles single choice with just A and B", () => {
    const text = "True or false?\nA. True\nB. False";
    const result = parseOptions(text);
    expect(result).toHaveLength(2);
  });

  it("handles lettered options with parenthesis format", () => {
    const text = "Pick one:\nA) Alpha\nB) Beta\nC) Gamma";
    const result = parseOptions(text);
    expect(result).toHaveLength(3);
  });

  it("handles lettered options with colon format", () => {
    const text = "Pick one:\nA: Alpha\nB: Beta";
    const result = parseOptions(text);
    expect(result).toHaveLength(2);
  });
});

describe("extractOptionKey", () => {
  it("extracts letter key from lettered option", () => {
    expect(extractOptionKey("A. could; can")).toBe("A");
  });

  it("extracts numeric key from numeric option", () => {
    expect(extractOptionKey("1. First option")).toBe("1");
  });

  it("returns trimmed option when no marker is found", () => {
    expect(extractOptionKey("no marker")).toBe("no marker");
  });
});

describe("AnswerInput option-letter keyboard shortcuts", () => {
  const ABC = ["A. One", "B. Two", "C. Three"];

  function renderSingleChoice(overrides: { value?: string; options?: string[]; disabled?: boolean } = {}) {
    const onChange = vi.fn();
    const result = render(
      <AnswerInput
        problemType="single-choice"
        value={overrides.value ?? ""}
        onChange={onChange}
        options={overrides.options ?? ABC}
        disabled={overrides.disabled}
      />,
    );
    return { ...result, onChange };
  }

  function renderMultiChoice(overrides: { value?: string; options?: string[]; disabled?: boolean } = {}) {
    const onChange = vi.fn();
    const result = render(
      <AnswerInput
        problemType="multi-choice"
        value={overrides.value ?? ""}
        onChange={onChange}
        options={overrides.options ?? ABC}
        disabled={overrides.disabled}
      />,
    );
    return { ...result, onChange };
  }

  it("selects the matching single-choice option on a lowercase letter", () => {
    const { onChange } = renderSingleChoice();
    fireEvent.keyDown(window, { key: "a" });
    expect(onChange).toHaveBeenCalledWith("A");
  });

  it("selects the matching single-choice option on an uppercase Shift letter", () => {
    const { onChange } = renderSingleChoice();
    fireEvent.keyDown(window, { key: "A", shiftKey: true });
    expect(onChange).toHaveBeenCalledWith("A");
  });

  it("matches a single-letter option identifier outside the A-E range", () => {
    const { onChange } = renderSingleChoice({ options: ["F. Sixth", "G. Seventh"] });
    fireEvent.keyDown(window, { key: "f" });
    expect(onChange).toHaveBeenCalledWith("F");
  });

  it("ignores unmatched letters", () => {
    const { onChange } = renderSingleChoice();
    fireEvent.keyDown(window, { key: "z" });
    expect(onChange).not.toHaveBeenCalled();
  });

  it("ignores numeric option identifiers", () => {
    const { onChange } = renderSingleChoice({ options: ["1. First", "2. Second"] });
    fireEvent.keyDown(window, { key: "1" });
    expect(onChange).not.toHaveBeenCalled();
  });

  it("ignores Ctrl, Meta, and Alt modifiers", () => {
    const { onChange } = renderSingleChoice();
    fireEvent.keyDown(window, { key: "a", ctrlKey: true });
    fireEvent.keyDown(window, { key: "a", metaKey: true });
    fireEvent.keyDown(window, { key: "a", altKey: true });
    expect(onChange).not.toHaveBeenCalled();
  });

  it("ignores repeated keydown events", () => {
    const { onChange } = renderSingleChoice();
    fireEvent.keyDown(window, { key: "a", repeat: true });
    expect(onChange).not.toHaveBeenCalled();
  });

  it("does not handle shortcuts when disabled", () => {
    const { onChange } = renderSingleChoice({ disabled: true });
    fireEvent.keyDown(window, { key: "a" });
    expect(onChange).not.toHaveBeenCalled();
  });

  it("does not handle shortcuts for non-choice question types", () => {
    const onChange = vi.fn();
    render(<AnswerInput problemType="short-answer" value="" onChange={onChange} options={ABC} />);
    fireEvent.keyDown(window, { key: "a" });
    expect(onChange).not.toHaveBeenCalled();
  });

  it("ignores keys originating from a text input", () => {
    const { onChange } = renderSingleChoice();
    const textInput = document.createElement("input");
    textInput.type = "text";
    document.body.appendChild(textInput);
    fireEvent.keyDown(textInput, { key: "a" });
    expect(onChange).not.toHaveBeenCalled();
    document.body.removeChild(textInput);
  });

  it("treats a focused radio control as an eligible target", () => {
    const { container, onChange } = renderSingleChoice();
    const radio = container.querySelector('input[type="radio"]') as HTMLInputElement;
    fireEvent.keyDown(radio, { key: "a" });
    expect(onChange).toHaveBeenCalledWith("A");
  });

  it("toggles a multi-choice option on and then off", () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <AnswerInput problemType="multi-choice" value="" onChange={onChange} options={ABC} />,
    );
    fireEvent.keyDown(window, { key: "a" });
    expect(onChange).toHaveBeenCalledWith("A");

    rerender(<AnswerInput problemType="multi-choice" value="A" onChange={onChange} options={ABC} />);
    fireEvent.keyDown(window, { key: "a" });
    expect(onChange).toHaveBeenCalledWith("");
  });

  it("toggles a second multi-choice option alongside an already-checked one", () => {
    const onChange = vi.fn();
    render(<AnswerInput problemType="multi-choice" value="A" onChange={onChange} options={ABC} />);
    fireEvent.keyDown(window, { key: "b" });
    expect(onChange).toHaveBeenCalledWith("A, B");
  });

  it("removes the keyboard listener on unmount", () => {
    const onChange = vi.fn();
    const { unmount } = render(
      <AnswerInput problemType="single-choice" value="" onChange={onChange} options={ABC} />,
    );
    unmount();
    fireEvent.keyDown(window, { key: "a" });
    expect(onChange).not.toHaveBeenCalled();
  });

  it("does not call onChange more than once for a single key press after a rerender", () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <AnswerInput problemType="single-choice" value="" onChange={onChange} options={ABC} />,
    );
    rerender(<AnswerInput problemType="single-choice" value="" onChange={onChange} options={ABC} />);
    fireEvent.keyDown(window, { key: "a" });
    expect(onChange).toHaveBeenCalledTimes(1);
  });
});
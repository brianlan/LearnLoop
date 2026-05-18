import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TagInput } from "./TagInput";

describe("TagInput", () => {
  it("renders with existing tags as bubbles", () => {
    render(<TagInput tags={["math", "algebra"]} onChange={vi.fn()} testId="tags-input" />);

    expect(screen.getByTestId("tags-input")).toBeInTheDocument();
    expect(screen.getByTestId("tags-input-tag-math")).toBeInTheDocument();
    expect(screen.getByTestId("tags-input-tag-algebra")).toBeInTheDocument();
  });

  it("removes a tag when × is clicked", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();

    render(<TagInput tags={["math", "algebra"]} onChange={onChange} testId="tags-input" />);

    await user.click(screen.getByTestId("tags-input-remove-math"));

    expect(onChange).toHaveBeenCalledWith(["algebra"]);
  });

  it("adds a tag when Enter is pressed", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();

    render(<TagInput tags={["math"]} onChange={onChange} testId="tags-input" />);

    await user.type(screen.getByTestId("tags-input-field"), "geometry{enter}");

    expect(onChange).toHaveBeenCalledWith(["math", "geometry"]);
  });

  it("does not add duplicate tags", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();

    render(<TagInput tags={["math"]} onChange={onChange} testId="tags-input" />);

    await user.type(screen.getByTestId("tags-input-field"), "math{enter}");

    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByTestId("tags-input-field")).toHaveValue("");
  });

  it("does not add empty tags", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();

    render(<TagInput tags={[]} onChange={onChange} testId="tags-input" />);

    await user.type(screen.getByTestId("tags-input-field"), "   {enter}");

    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByTestId("tags-input-field")).toHaveValue("");
  });

  it("shows filtered suggestions when typing", async () => {
    const user = userEvent.setup();

    render(
      <TagInput
        tags={["math"]}
        onChange={vi.fn()}
        suggestions={["math", "matrix", "matter", "geometry"]}
        testId="tags-input"
      />,
    );

    await user.type(screen.getByTestId("tags-input-field"), "ma");

    expect(screen.getByTestId("tags-input-suggestions")).toBeInTheDocument();
    expect(screen.getByTestId("tags-input-suggestion-matrix")).toBeInTheDocument();
    expect(screen.getByTestId("tags-input-suggestion-matter")).toBeInTheDocument();
    expect(screen.queryByTestId("tags-input-suggestion-math")).not.toBeInTheDocument();
    expect(screen.queryByTestId("tags-input-suggestion-geometry")).not.toBeInTheDocument();
  });

  it("adds tag when suggestion is clicked", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();

    render(
      <TagInput tags={[]} onChange={onChange} suggestions={["algebra", "geometry"]} testId="tags-input" />,
    );

    await user.type(screen.getByTestId("tags-input-field"), "al");
    await user.click(screen.getByTestId("tags-input-suggestion-algebra"));

    expect(onChange).toHaveBeenCalledWith(["algebra"]);
  });

  it("hides suggestions when Escape is pressed", async () => {
    const user = userEvent.setup();

    render(<TagInput tags={[]} onChange={vi.fn()} suggestions={["algebra"]} testId="tags-input" />);

    await user.type(screen.getByTestId("tags-input-field"), "al");
    expect(screen.getByTestId("tags-input-suggestions")).toBeInTheDocument();

    await user.type(screen.getByTestId("tags-input-field"), "{escape}");

    expect(screen.queryByTestId("tags-input-suggestions")).not.toBeInTheDocument();
    expect(screen.getByTestId("tags-input-field")).toHaveValue("");
  });

  it("removes last tag on Backspace when input is empty", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();

    render(<TagInput tags={["math", "algebra"]} onChange={onChange} testId="tags-input" />);

    await user.click(screen.getByTestId("tags-input-field"));
    await user.type(screen.getByTestId("tags-input-field"), "{backspace}");

    expect(onChange).toHaveBeenCalledWith(["math"]);
  });

  it("calls onChange correctly for all operations", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();

    render(
      <TagInput
        tags={["math"]}
        onChange={onChange}
        suggestions={["algebra", "geometry"]}
        testId="tags-input"
      />,
    );

    await user.click(screen.getByTestId("tags-input-remove-math"));
    await user.type(screen.getByTestId("tags-input-field"), "geometry{enter}");

    expect(onChange).toHaveBeenNthCalledWith(1, []);
    expect(onChange).toHaveBeenNthCalledWith(2, ["math", "geometry"]);
  });
});

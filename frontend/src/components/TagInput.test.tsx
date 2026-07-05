import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
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

  it("keeps focus on the input after adding a tag", async () => {
    const user = userEvent.setup();

    render(<TagInput tags={[]} onChange={vi.fn()} testId="tags-input" />);

    const input = screen.getByTestId("tags-input-field");
    await user.type(input, "geometry{enter}");

    await waitFor(() => {
      expect(input).toHaveFocus();
    });
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

  describe("comma-separated batch input", () => {
    it("creates multiple tags from comma-separated input", async () => {
      const user = userEvent.setup();
      const onChange = vi.fn();

      render(<TagInput tags={["math"]} onChange={onChange} testId="tags-input" />);

      await user.type(screen.getByTestId("tags-input-field"), "algebra, linear algebra, matrices{enter}");

      expect(onChange).toHaveBeenCalledWith(["math", "algebra", "linear algebra", "matrices"]);
    });

    it("ignores empty segments from trailing commas", async () => {
      const user = userEvent.setup();
      const onChange = vi.fn();

      render(<TagInput tags={[]} onChange={onChange} testId="tags-input" />);

      await user.type(screen.getByTestId("tags-input-field"), "algebra, {enter}");

      expect(onChange).toHaveBeenCalledWith(["algebra"]);
    });

    it("ignores empty segments from double commas", async () => {
      const user = userEvent.setup();
      const onChange = vi.fn();

      render(<TagInput tags={[]} onChange={onChange} testId="tags-input" />);

      await user.type(screen.getByTestId("tags-input-field"), "algebra,,matrices{enter}");

      expect(onChange).toHaveBeenCalledWith(["algebra", "matrices"]);
    });

    it("skips duplicate tags already in the list", async () => {
      const user = userEvent.setup();
      const onChange = vi.fn();

      render(<TagInput tags={["math"]} onChange={onChange} testId="tags-input" />);

      await user.type(screen.getByTestId("tags-input-field"), "math, algebra{enter}");

      expect(onChange).toHaveBeenCalledWith(["math", "algebra"]);
    });

    it("deduplicates within comma-separated input", async () => {
      const user = userEvent.setup();
      const onChange = vi.fn();

      render(<TagInput tags={[]} onChange={onChange} testId="tags-input" />);

      await user.type(screen.getByTestId("tags-input-field"), "algebra, algebra, matrices{enter}");

      expect(onChange).toHaveBeenCalledWith(["algebra", "matrices"]);
    });

    it("selects highlighted suggestion instead of splitting when dropdown is open", async () => {
      const user = userEvent.setup();
      const onChange = vi.fn();

      render(
        <TagInput tags={[]} onChange={onChange} suggestions={["algebra"]} testId="tags-input" />,
      );

      await user.type(screen.getByTestId("tags-input-field"), "al");
      await user.type(screen.getByTestId("tags-input-field"), "{enter}");

      expect(onChange).toHaveBeenCalledWith(["algebra"]);
    });

    it("splits on commas when no suggestion is highlighted", async () => {
      const user = userEvent.setup();
      const onChange = vi.fn();

      render(<TagInput tags={[]} onChange={onChange} testId="tags-input" />);

      await user.type(screen.getByTestId("tags-input-field"), "trigonometry, calculus{enter}");

      expect(onChange).toHaveBeenCalledWith(["trigonometry", "calculus"]);
    });

    it("clicking a suggestion adds only that tag, leaving remaining text in input", async () => {
      const user = userEvent.setup();
      const onChange = vi.fn();

      render(
        <TagInput tags={[]} onChange={onChange} suggestions={["geometry"]} testId="tags-input" />,
      );

      await user.type(screen.getByTestId("tags-input-field"), "geo");
      await user.click(screen.getByTestId("tags-input-suggestion-geometry"));

      expect(onChange).toHaveBeenCalledWith(["geometry"]);
    });

    it("clears input after adding comma-separated tags", async () => {
      const user = userEvent.setup();
      const onChange = vi.fn();

      render(<TagInput tags={[]} onChange={onChange} testId="tags-input" />);

      await user.type(screen.getByTestId("tags-input-field"), "a, b, c{enter}");

      expect(screen.getByTestId("tags-input-field")).toHaveValue("");
    });
  });

  describe("semicolon-separated input", () => {
    it("creates a tag when semicolon is pressed", async () => {
      const user = userEvent.setup();
      const onChange = vi.fn();

      render(<TagInput tags={["math"]} onChange={onChange} testId="tags-input" />);

      await user.type(screen.getByTestId("tags-input-field"), "geometry;");

      expect(onChange).toHaveBeenCalledWith(["math", "geometry"]);
    });

    it("does not include semicolon in the tag name", async () => {
      const user = userEvent.setup();
      const onChange = vi.fn();

      render(<TagInput tags={[]} onChange={onChange} testId="tags-input" />);

      await user.type(screen.getByTestId("tags-input-field"), "Grade4;");

      expect(onChange).toHaveBeenCalledWith(["Grade4"]);
      expect(screen.getByTestId("tags-input-field")).toHaveValue("");
    });

    it("clears input after semicolon commit", async () => {
      const user = userEvent.setup();
      const onChange = vi.fn();

      render(<TagInput tags={[]} onChange={onChange} testId="tags-input" />);

      await user.type(screen.getByTestId("tags-input-field"), "vocabulary;");

      expect(screen.getByTestId("tags-input-field")).toHaveValue("");
    });

    it("creates multiple tags from semicolon-separated batch input", async () => {
      const user = userEvent.setup();
      const onChange = vi.fn();

      render(<TagInput tags={[]} onChange={onChange} testId="tags-input" />);

      await user.type(screen.getByTestId("tags-input-field"), "Grade4; grammar; vocabulary{enter}");

      expect(onChange).toHaveBeenCalledTimes(3);
      expect(onChange).toHaveBeenNthCalledWith(1, ["Grade4"]);
      expect(onChange).toHaveBeenNthCalledWith(2, ["grammar"]);
      expect(onChange).toHaveBeenNthCalledWith(3, ["vocabulary"]);
    });

    it("does not create empty tags from repeated semicolons", async () => {
      const user = userEvent.setup();
      const onChange = vi.fn();

      render(<TagInput tags={[]} onChange={onChange} testId="tags-input" />);

      await user.type(screen.getByTestId("tags-input-field"), "Grade4;; grammar;{enter}");

      expect(onChange).toHaveBeenCalledTimes(2);
      expect(onChange).toHaveBeenNthCalledWith(1, ["Grade4"]);
      expect(onChange).toHaveBeenNthCalledWith(2, ["grammar"]);
    });

    it("does not create empty tag from semicolon with only whitespace", async () => {
      const user = userEvent.setup();
      const onChange = vi.fn();

      render(<TagInput tags={[]} onChange={onChange} testId="tags-input" />);

      await user.type(screen.getByTestId("tags-input-field"), "   ;");

      expect(onChange).not.toHaveBeenCalled();
      expect(screen.getByTestId("tags-input-field")).toHaveValue("");
    });

    it("skips duplicate tags when using semicolon", async () => {
      const user = userEvent.setup();
      const onChange = vi.fn();

      render(<TagInput tags={["math"]} onChange={onChange} testId="tags-input" />);

      await user.type(screen.getByTestId("tags-input-field"), "math;");

      expect(onChange).not.toHaveBeenCalled();
      expect(screen.getByTestId("tags-input-field")).toHaveValue("");
    });

    it("creates tags from mixed comma and semicolon input", async () => {
      const user = userEvent.setup();
      const onChange = vi.fn();

      render(<TagInput tags={[]} onChange={onChange} testId="tags-input" />);

      await user.type(screen.getByTestId("tags-input-field"), "algebra; geometry, calculus{enter}");

      expect(onChange).toHaveBeenCalledTimes(2);
      expect(onChange).toHaveBeenNthCalledWith(1, ["algebra"]);
      expect(onChange).toHaveBeenNthCalledWith(2, ["geometry", "calculus"]);
    });
  });

  describe("accessibility", () => {
    it("has combobox role on the input", () => {
      render(<TagInput tags={[]} onChange={vi.fn()} testId="tags-input" />);

      const input = screen.getByTestId("tags-input-field");
      expect(input).toHaveAttribute("role", "combobox");
      expect(input).toHaveAttribute("aria-expanded", "false");
      expect(input).toHaveAttribute("aria-haspopup", "listbox");
      expect(input).toHaveAttribute("aria-autocomplete", "list");
    });

    it("sets aria-expanded to true when suggestions are shown", async () => {
      const user = userEvent.setup();

      render(
        <TagInput tags={[]} onChange={vi.fn()} suggestions={["algebra", "geometry"]} testId="tags-input" />,
      );

      const input = screen.getByTestId("tags-input-field");
      expect(input).toHaveAttribute("aria-expanded", "false");

      await user.type(input, "al");

      expect(input).toHaveAttribute("aria-expanded", "true");
      expect(input).toHaveAttribute("aria-controls", "tags-input-suggestions");
    });

    it("has listbox role on the suggestions dropdown", async () => {
      const user = userEvent.setup();

      render(<TagInput tags={[]} onChange={vi.fn()} suggestions={["algebra"]} testId="tags-input" />);

      await user.type(screen.getByTestId("tags-input-field"), "al");

      const listbox = screen.getByTestId("tags-input-suggestions");
      expect(listbox).toHaveAttribute("role", "listbox");
    });

    it("has option role with aria-selected on suggestion items", async () => {
      const user = userEvent.setup();

      render(
        <TagInput tags={[]} onChange={vi.fn()} suggestions={["algebra", "algorithm"]} testId="tags-input" />,
      );

      await user.type(screen.getByTestId("tags-input-field"), "al");

      const firstOption = screen.getByTestId("tags-input-suggestion-algebra");
      expect(firstOption).toHaveAttribute("role", "option");
      expect(firstOption).toHaveAttribute("aria-selected", "true");

      const secondOption = screen.getByTestId("tags-input-suggestion-algorithm");
      expect(secondOption).toHaveAttribute("role", "option");
      expect(secondOption).toHaveAttribute("aria-selected", "false");
    });

    it("sets aria-activedescendant to the highlighted suggestion", async () => {
      const user = userEvent.setup();

      render(
        <TagInput tags={[]} onChange={vi.fn()} suggestions={["algebra", "algorithm"]} testId="tags-input" />,
      );

      await user.type(screen.getByTestId("tags-input-field"), "al");

      const input = screen.getByTestId("tags-input-field");
      expect(input).toHaveAttribute("aria-activedescendant", "tags-input-suggestion-algebra");

      await user.type(input, "{arrowDown}");

      expect(input).toHaveAttribute("aria-activedescendant", "tags-input-suggestion-algorithm");
    });
  });
});

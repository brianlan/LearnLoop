import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { LatexText } from "./LatexText";

describe("LatexText", () => {
  it("renders plain text without LaTeX unchanged", () => {
    render(<LatexText text="Hello world" />);
    expect(screen.getByText("Hello world")).toBeInTheDocument();
  });

  it("renders inline LaTeX", () => {
    const { container } = render(<LatexText text="The value $x^2$ is squared" />);
    const html = container.innerHTML;
    expect(html).toContain("katex-wrapper");
    expect(html).toContain("squared");
  });

  it("renders display LaTeX", () => {
    const { container } = render(<LatexText text="$$x^2 + y^2 = z^2$$" />);
    const html = container.innerHTML;
    expect(html).toContain("katex-display");
  });

  it("renders mixed text and LaTeX", () => {
    const { container } = render(
      <LatexText text="Given $a=1$, compute $$a^2$$" />
    );
    const html = container.innerHTML;
    expect(html).toContain("katex-wrapper");
    expect(html).toContain("katex-display");
    expect(html).toContain("Given");
    expect(html).toContain("compute");
  });

  it("falls back gracefully for invalid LaTeX", () => {
    const { container } = render(<LatexText text="$\invalid$" />);
    const html = container.innerHTML;
    expect(html).toContain("katex-wrapper");
  });

  it("escapes HTML special characters in plain text", () => {
    const { container } = render(<LatexText text="<script>alert(1)</script>" />);
    const html = container.innerHTML;
    expect(html).not.toContain("<script>");
    expect(html).toContain("&lt;script&gt;");
  });

  it("escapes ampersands in plain text", () => {
    const { container } = render(<LatexText text="A & B" />);
    const html = container.innerHTML;
    expect(html).toContain("A &amp; B");
  });

  it("renders empty string without errors", () => {
    const { container } = render(<LatexText text="" />);
    const div = container.firstElementChild as HTMLElement;
    expect(div.innerHTML).toBe("");
  });

  it("converts newlines to br tags in plain text", () => {
    const { container } = render(<LatexText text={"line1\nline2"} />);
    const html = container.innerHTML;
    expect(html).toContain("<br>");
  });

  it("applies style and className props", () => {
    const { container } = render(
      <LatexText
        text="test"
        style={{ color: "red" }}
        className="custom-class"
      />
    );
    const div = container.firstElementChild as HTMLElement;
    expect(div.style.color).toBe("red");
    expect(div.classList.contains("custom-class")).toBe(true);
  });

  it("passes through data-testid", () => {
    render(<LatexText text="hello" data-testid="latex-output" />);
    expect(screen.getByTestId("latex-output")).toBeInTheDocument();
  });
});

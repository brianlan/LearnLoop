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

  it("does not treat currency dollar signs as LaTeX", () => {
    const { container } = render(<LatexText text="Price is $5.00$" />);
    expect(container.innerHTML).not.toContain("katex-wrapper");
    expect(container.textContent).toContain("$5.00$");
  });

  it("does not treat integer dollar amounts as LaTeX", () => {
    const { container } = render(<LatexText text="I have $5$ and $10$" />);
    expect(container.innerHTML).not.toContain("katex-wrapper");
  });

  it("renders inline LaTeX at start of string", () => {
    const { container } = render(<LatexText text="$x^2$ is squared" />);
    expect(container.innerHTML).toContain("katex-wrapper");
  });

  it("renders inline LaTeX followed by punctuation", () => {
    const { container } = render(<LatexText text="Value $x^2$, then" />);
    expect(container.innerHTML).toContain("katex-wrapper");
  });

  it("renders inline LaTeX after opening parenthesis", () => {
    const { container } = render(<LatexText text="($x^2$)" />);
    expect(container.innerHTML).toContain("katex-wrapper");
  });

  it("renders LaTeX starting with a digit", () => {
    const { container } = render(<LatexText text="$2+2$" />);
    expect(container.innerHTML).toContain("katex-wrapper");
  });

  it("renders polynomial LaTeX starting with a digit", () => {
    const { container } = render(<LatexText text="$3x^2 + 2x + 1$" />);
    expect(container.innerHTML).toContain("katex-wrapper");
  });

  it("renders decimal in math mode", () => {
    const { container } = render(<LatexText text="$0.5$" />);
    expect(container.innerHTML).toContain("katex-wrapper");
  });

  it("renders factorial starting with digit", () => {
    const { container } = render(<LatexText text="$100!$" />);
    expect(container.innerHTML).toContain("katex-wrapper");
  });
});

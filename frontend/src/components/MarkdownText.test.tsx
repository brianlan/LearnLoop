import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { MarkdownText } from "./MarkdownText";

describe("MarkdownText", () => {
  it("renders plain text", () => {
    render(<MarkdownText content="Hello world" data-testid="md" />);
    expect(screen.getByTestId("md")).toHaveTextContent("Hello world");
  });

  it("renders bold text as strong element", () => {
    render(<MarkdownText content="**important**" data-testid="md" />);
    expect(screen.getByTestId("md")).toContainHTML("<strong>important</strong>");
  });

  it("renders bullet list items", () => {
    render(
      <MarkdownText
        content={"- Step one\n- Step two"}
        data-testid="md"
      />
    );
    const listItems = screen.getByTestId("md").querySelectorAll("li");
    expect(listItems).toHaveLength(2);
    expect(listItems[0]).toHaveTextContent("Step one");
    expect(listItems[1]).toHaveTextContent("Step two");
  });

  it("renders numbered list items", () => {
    render(
      <MarkdownText
        content={"1. First\n2. Second"}
        data-testid="md"
      />
    );
    const listItems = screen.getByTestId("md").querySelectorAll("li");
    expect(listItems).toHaveLength(2);
    expect(listItems[0]).toHaveTextContent("First");
    expect(listItems[1]).toHaveTextContent("Second");
  });

  it("renders inline math with KaTeX", () => {
    render(<MarkdownText content="$x^2$" data-testid="md" />);
    const md = screen.getByTestId("md");
    // rehype-katex renders math as span.katex or span.katex-wrapper
    expect(md.querySelector(".katex-wrapper, .katex")).toBeInTheDocument();
  });

  it("renders display math with KaTeX", () => {
    render(<MarkdownText content={"$$\nx^2 + y^2 = z^2\n$$"} data-testid="md" />);
    const md = screen.getByTestId("md");
    expect(md.querySelector(".katex-display")).toBeInTheDocument();
  });

  it("renders inline code", () => {
    render(<MarkdownText content="Use `const` for constants" data-testid="md" />);
    const codeEl = screen.getByTestId("md").querySelector("code");
    expect(codeEl).toBeInTheDocument();
    expect(codeEl).toHaveTextContent("const");
  });

  it("renders fenced code blocks", () => {
    render(
      <MarkdownText
        content={"```js\nconst x = 1;\n```"}
        data-testid="md"
      />
    );
    const preEl = screen.getByTestId("md").querySelector("pre");
    expect(preEl).toBeInTheDocument();
    expect(screen.getByTestId("md").textContent).toContain("const x = 1;");
  });

  it("renders links with safe attributes", () => {
    render(<MarkdownText content="[example](https://example.com)" data-testid="md" />);
    const link = screen.getByTestId("md").querySelector("a");
    expect(link).toHaveAttribute("href", "https://example.com");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(link).toHaveTextContent("example");
  });

  it("does not render raw HTML", () => {
    render(<MarkdownText content="<script>alert(1)</script>" data-testid="md" />);
    const scriptEl = screen.getByTestId("md").querySelector("script");
    expect(scriptEl).not.toBeInTheDocument();
    expect(screen.getByTestId("md").textContent).toContain("script");
  });
});

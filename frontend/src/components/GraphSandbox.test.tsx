import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { GraphSandbox, validateDsl } from "./GraphSandbox";

describe("GraphSandbox", () => {
  beforeEach(() => {
    global.URL.createObjectURL = vi.fn(() => "blob:test-url");
    global.URL.revokeObjectURL = vi.fn();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the sandbox container", () => {
    render(<GraphSandbox dsl="" />);
    expect(screen.getByTestId("graph-sandbox")).toBeInTheDocument();
  });

  it("renders an iframe with sandbox attribute", () => {
    render(<GraphSandbox dsl="" />);
    const iframe = screen.getByTestId("jsxgraph-iframe");
    expect(iframe).toBeInTheDocument();
    expect(iframe).toHaveAttribute("sandbox", "allow-scripts");
  });

  it("accepts custom width and height", () => {
    const { container } = render(<GraphSandbox dsl="" width={500} height={300} />);
    const sandbox = container.querySelector('[data-testid="graph-sandbox"]') as HTMLElement;
    expect(sandbox.style.width).toBe("500px");
    expect(sandbox.style.height).toBe("300px");
  });

  it("accepts string width values", () => {
    const { container } = render(<GraphSandbox dsl="" width="80%" height={300} />);
    const sandbox = container.querySelector('[data-testid="graph-sandbox"]') as HTMLElement;
    expect(sandbox.style.width).toBe("80%");
  });

  it("renders without error when dsl is empty", () => {
    render(<GraphSandbox dsl="" />);
    expect(screen.getByTestId("jsxgraph-iframe")).toBeInTheDocument();
  });

  it("renders with valid JSXGraph DSL", () => {
    render(<GraphSandbox dsl="var p = board.create('point', [0, 0]);" />);
    expect(screen.getByTestId("jsxgraph-iframe")).toBeInTheDocument();
  });

  it("allows the supported graph DSL subset", () => {
    const dsl = [
      "board.setBoundingBox([-1, 2, 6, -2])",
      "var A = board.create('point', [0, 0], {name:'A'})",
      "var B = board.create('point', [5, 0], {name:'B'})",
      "board.create('segment', [A, B], {strokeWidth:2})",
      "board.create('text', [2.5, 0.3, '490米'], {anchorX:'middle', fontSize:12})",
    ].join(";");

    expect(validateDsl(dsl)).toBeNull();
  });

  it.each([
    "fetch('/api/private'); board.create('point', [0, 0]);",
    "while (true) { board.create('point', [0, 0]); }",
    "window.location = 'https://example.com';",
    "new Function('return document.cookie')();",
    "board.constructor.constructor('return window')();",
    "board.create('functiongraph', [function(x) { return x; }, -1, 1]);",
    "board.create('point', [1 + 2, 0]);",
  ])("rejects unsafe graph DSL: %s", (dsl) => {
    expect(validateDsl(dsl)).not.toBeNull();
  });
});

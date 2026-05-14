import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { GraphSandbox } from "./GraphSandbox";

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
});

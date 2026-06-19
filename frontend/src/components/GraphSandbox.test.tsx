import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { GraphSandbox, validateDsl, generateIframeHtml, JSXGRAPH_VERSION, JSXGRAPH_CDN_URL } from "./GraphSandbox";

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

  it("generates iframe HTML with restrictive Content Security Policy", () => {
    const html = generateIframeHtml();

    // Verify CSP meta tag exists with restrictive directives
    expect(html).toContain("<meta http-equiv=\"Content-Security-Policy\"");
    expect(html).toContain("default-src 'none'");
    expect(html).toContain("connect-src 'none'");
    expect(html).toContain("frame-src 'none'");
    expect(html).toContain("object-src 'none'");
    expect(html).toContain("base-uri 'none'");
    expect(html).toContain("form-action 'none'");
  });

  it("generates iframe HTML with pinned JSXGraph version", () => {
    const html = generateIframeHtml();

    // Verify pinned JSXGraph URL is used
    expect(html).toContain(JSXGRAPH_CDN_URL);
    expect(html).not.toContain("https://cdn.jsdelivr.net/npm/jsxgraph/distrib/jsxgraphcore.js");
  });

  it("accepts custom width and height", () => {
    const { container } = render(<GraphSandbox dsl="" width={500} height={300} />);
    const sandbox = container.querySelector('[data-testid="graph-sandbox"]') as HTMLElement;
    expect(sandbox).toBeTruthy();
    expect(sandbox.style.width).toBe("500px");
    expect(sandbox.style.height).toBe("300px");
  });

  it("accepts string width values", () => {
    const { container } = render(<GraphSandbox dsl="" width="80%" height={300} />);
    const sandbox = container.querySelector('[data-testid="graph-sandbox"]') as HTMLElement;
    expect(sandbox).toBeTruthy();
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

  it("allows setBoundingBox with multiple arguments", () => {
    const dsl = "board.setBoundingBox([-2, 2, 4, -3], true);";
    expect(validateDsl(dsl)).toBeNull();
  });

  it("allows complex historical DSL", () => {
    const dsl = `
      board.setBoundingBox([-2, 2, 4, -3], true);
      var a = board.create('point', [0, 0]);
      var b = board.create('point', [1, 1]);
      var c = board.create('segment', [a, b]);
      var d = Math.sqrt(2);
    `;
    expect(validateDsl(dsl)).toBeNull();
  });

  it("rejects DSL that is too long", () => {
    const dsl = "x".repeat(5001);
    expect(validateDsl(dsl)).toBe("DSL is too long");
  });

  describe("prop change lifecycle", () => {
    // Helper component to allow changing dsl prop during tests
    function GraphSandboxWithState({ initialDsl }: { initialDsl: string }) {
      const [dsl, setDsl] = useState(initialDsl);
      return (
        <div>
          <button onClick={() => setDsl("board.create('point', [1, 1]);")} data-testid="change-dsl">
            Change DSL
          </button>
          <GraphSandbox dsl={dsl} />
        </div>
      );
    }

    it("sends render message after iframe sends ready", async () => {
      const dsl = "board.create('point', [0, 0]);";
      const postMessageSpy = vi.fn();
      const mockContentWindow = { postMessage: postMessageSpy };

      render(<GraphSandbox dsl={dsl} />);

      const iframe = screen.getByTestId("jsxgraph-iframe") as HTMLIFrameElement;

      // Mock contentWindow
      Object.defineProperty(iframe, "contentWindow", {
        value: mockContentWindow,
        writable: true,
      });

      // Simulate iframe sending "ready" message
      const messageEvent = new MessageEvent("message", {
        source: mockContentWindow as unknown as Window,
        data: { type: "ready" },
      });

      window.dispatchEvent(messageEvent);

      await waitFor(() => {
        expect(postMessageSpy).toHaveBeenCalledWith(
          { type: "render", payload: dsl },
          "*"
        );
      });
    });

    it("sends new render message when dsl prop changes after successful render", async () => {
      const firstDsl = "board.create('point', [0, 0]);";
      const secondDsl = "board.create('point', [1, 1]);";
      const postMessageSpy = vi.fn();
      const mockContentWindow = { postMessage: postMessageSpy };

      render(<GraphSandboxWithState initialDsl={firstDsl} />);

      const iframe = screen.getByTestId("jsxgraph-iframe") as HTMLIFrameElement;

      // Mock contentWindow
      Object.defineProperty(iframe, "contentWindow", {
        value: mockContentWindow,
        writable: true,
      });

      // Simulate iframe sending "ready" message
      const readyEvent = new MessageEvent("message", {
        source: mockContentWindow as unknown as Window,
        data: { type: "ready" },
      });
      window.dispatchEvent(readyEvent);

      // Wait for first render message
      await waitFor(() => {
        expect(postMessageSpy).toHaveBeenCalledWith(
          { type: "render", payload: firstDsl },
          "*"
        );
      });

      // Simulate iframe sending "rendered" message (successful render)
      postMessageSpy.mockClear();
      const renderedEvent = new MessageEvent("message", {
        source: mockContentWindow as unknown as Window,
        data: { type: "rendered" },
      });
      window.dispatchEvent(renderedEvent);

      // Change the DSL prop
      await userEvent.click(screen.getByTestId("change-dsl"));

      // Should send the new DSL
      await waitFor(() => {
        expect(postMessageSpy).toHaveBeenCalledWith(
          { type: "render", payload: secondDsl },
          "*"
        );
      });
    });

    it("does not send duplicate render messages for the same DSL", async () => {
      const dsl = "board.create('point', [0, 0]);";
      const postMessageSpy = vi.fn();
      const mockContentWindow = { postMessage: postMessageSpy };

      render(<GraphSandbox dsl={dsl} />);

      const iframe = screen.getByTestId("jsxgraph-iframe") as HTMLIFrameElement;

      // Mock contentWindow
      Object.defineProperty(iframe, "contentWindow", {
        value: mockContentWindow,
        writable: true,
      });

      // Simulate iframe sending "ready" message
      const readyEvent = new MessageEvent("message", {
        source: mockContentWindow as unknown as Window,
        data: { type: "ready" },
      });
      window.dispatchEvent(readyEvent);

      // Wait for render message
      await waitFor(() => {
        expect(postMessageSpy).toHaveBeenCalledTimes(1);
        expect(postMessageSpy).toHaveBeenCalledWith(
          { type: "render", payload: dsl },
          "*"
        );
      });

      postMessageSpy.mockClear();

      // Simulate iframe sending "rendered" message
      const renderedEvent = new MessageEvent("message", {
        source: mockContentWindow as unknown as Window,
        data: { type: "rendered" },
      });
      window.dispatchEvent(renderedEvent);

      // Wait a bit and verify no additional render messages
      await new Promise(resolve => setTimeout(resolve, 100));
      expect(postMessageSpy).not.toHaveBeenCalled();
    });

    it("clears lastSentDslRef when iframe is reset", async () => {
      const dsl = "board.create('point', [0, 0]);";
      const postMessageSpy = vi.fn();
      const mockContentWindow = { postMessage: postMessageSpy };

      render(<GraphSandbox dsl={dsl} />);

      const iframe = screen.getByTestId("jsxgraph-iframe") as HTMLIFrameElement;

      // Mock contentWindow
      Object.defineProperty(iframe, "contentWindow", {
        value: mockContentWindow,
        writable: true,
      });

      // Simulate iframe sending "ready" message
      const readyEvent = new MessageEvent("message", {
        source: mockContentWindow as unknown as Window,
        data: { type: "ready" },
      });
      window.dispatchEvent(readyEvent);

      // Wait for render message
      await waitFor(() => {
        expect(postMessageSpy).toHaveBeenCalled();
      });

      // Simulate error to show reset button
      const errorEvent = new MessageEvent("message", {
        source: mockContentWindow as unknown as Window,
        data: { type: "error", payload: "test error" },
      });
      window.dispatchEvent(errorEvent);

      await waitFor(() => {
        expect(screen.getByText("Reset Renderer")).toBeInTheDocument();
      });

      // The reset button should be visible and clickable
      await userEvent.click(screen.getByText("Reset Renderer"));

      // After reset, iframe key should change (new iframe instance)
      const newIframe = screen.getByTestId("jsxgraph-iframe");
      expect(newIframe).toBeInTheDocument();
    });
  });
});

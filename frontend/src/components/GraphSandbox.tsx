import { useCallback, useEffect, useRef, useState } from "react";

// Security denylist for DSL validation
const DENYLIST_PATTERNS = [
  /fetch\s*\(/i,
  /XMLHttpRequest/i,
  /eval\s*\(/i,
  /import\s*\(/i,
  /<script/i,
  /document\.cookie/i,
  /window\.location/i,
  /localStorage/i,
  /sessionStorage/i,
];

// Timeout for rendering operations (30 seconds)
const RENDER_TIMEOUT_MS = 30000;

export interface GraphSandboxProps {
  /** The JSXGraph DSL code to render */
  dsl: string;
  /** Width of the container */
  width?: number | string;
  /** Height of the container */
  height?: number | string;
  /** Callback when rendering fails */
  onError?: (error: string) => void;
  /** Callback when rendering succeeds */
  onRender?: () => void;
}

/**
 * Validates DSL code against security denylist and JavaScript syntax.
 * Returns null if valid, error message if invalid.
 */
function validateDsl(dsl: string): string | null {
  for (const pattern of DENYLIST_PATTERNS) {
    if (pattern.test(dsl)) {
      return `DSL contains forbidden pattern: ${pattern.source}`;
    }
  }

  try {
    new Function("board", dsl);
  } catch (e) {
    if (e instanceof SyntaxError) {
      return `DSL syntax error: ${e.message}`;
    }
  }

  return null;
}

/**
 * Generates the iframe HTML content with JSXGraph loader.
 * This is loaded into the sandboxed iframe.
 */
function generateIframeHtml(): string {
  return `<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <title>JSXGraph Sandbox</title>
  <script src="https://cdn.jsdelivr.net/npm/jsxgraph/distrib/jsxgraphcore.js"></script>
  <style>
    html, body {
      margin: 0;
      padding: 0;
      width: 100%;
      height: 100%;
      overflow: hidden;
      font-family: sans-serif;
    }
    #jxgbox {
      width: 100%;
      height: 100%;
    }
    .error {
      color: #dc2626;
      padding: 16px;
      font-size: 14px;
    }
  </style>
</head>
<body>
  <div id="jxgbox"></div>
  <script>
    (function() {
      // Track board instance for cleanup
      let board = null;

      // Message handler for postMessage protocol
      function handleMessage(event) {
        const data = event.data;
        
        if (!data || typeof data !== 'object') {
          return;
        }

        switch (data.type) {
          case 'render':
            handleRender(data.payload);
            break;
          case 'clear':
            handleClear();
            break;
          default:
            console.warn('Unknown message type:', data.type);
        }
      }

      function handleRender(dsl) {
        try {
          // Clear any existing board
          if (board) {
            JXG.JSXGraph.freeBoard(board);
            board = null;
          }

          // Create new board
          board = JXG.JSXGraph.initBoard('jxgbox', {
            boundingbox: [-5, 5, 5, -5],
            axis: false,
            grid: false,
            showCopyright: false,
            showNavigation: true,
            keepaspectratio: true
          });

          // Execute the DSL in a controlled way
          // The DSL is expected to be a function that takes the board as parameter
          const dslFunction = new Function('board', dsl);
          dslFunction(board);

          // Notify parent of success
          parent.postMessage({ type: 'rendered' }, '*');
        } catch (error) {
          // Notify parent of error
          parent.postMessage({ 
            type: 'error', 
            payload: error instanceof Error ? error.message : String(error)
          }, '*');
        }
      }

      function handleClear() {
        if (board) {
          JXG.JSXGraph.freeBoard(board);
          board = null;
        }
        // Notify parent of clear completion
        parent.postMessage({ type: 'cleared' }, '*');
      }

      // Listen for messages from parent
      window.addEventListener('message', handleMessage);

      // Notify parent that sandbox is ready
      parent.postMessage({ type: 'ready' }, '*');

      // Cleanup on page unload
      window.addEventListener('beforeunload', function() {
        if (board) {
          JXG.JSXGraph.freeBoard(board);
        }
      });
    })();
  </script>
</body>
</html>`;
}

interface MessagePayload {
  type: string;
  payload?: string;
}

/**
 * GraphSandbox Component
 * 
 * Renders JSXGraph DSL in a sandboxed iframe with strict postMessage protocol.
 * 
 * Security features:
 * - iframe with sandbox="allow-scripts" (no allow-same-origin, no allow-forms)
 * - DSL validation against denylist patterns
 * - Timeout handling with iframe recreation
 * - No external API access from iframe
 */
export function GraphSandbox({
  dsl,
  width = "100%",
  height = 400,
  onError,
  onRender,
}: GraphSandboxProps) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [iframeKey, setIframeKey] = useState(0);
  const [status, setStatus] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [errorMessage, setErrorMessage] = useState<string>("");
  const renderTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Clear any pending timeout
  const clearRenderTimeout = useCallback(() => {
    if (renderTimeoutRef.current) {
      clearTimeout(renderTimeoutRef.current);
      renderTimeoutRef.current = null;
    }
  }, []);

  // Reset the iframe by incrementing key
  const resetIframe = useCallback(() => {
    clearRenderTimeout();
    setIframeKey((k) => k + 1);
    setStatus("idle");
    setErrorMessage("");
  }, [clearRenderTimeout]);

  // Handle postMessage from iframe
  useEffect(() => {
    function handleMessage(event: MessageEvent<MessagePayload>) {
      // Only accept messages from our iframe
      if (event.source !== iframeRef.current?.contentWindow) {
        return;
      }

      const data = event.data;
      if (!data || typeof data !== "object") {
        return;
      }

      switch (data.type) {
        case "ready":
          setStatus("ready");
          break;
        case "rendered":
          clearRenderTimeout();
          setStatus("idle");
          onRender?.();
          break;
        case "error":
          clearRenderTimeout();
          setStatus("error");
          const errorMsg = data.payload || "Unknown rendering error";
          setErrorMessage(errorMsg);
          onError?.(errorMsg);
          break;
        case "cleared":
          setStatus("idle");
          break;
      }
    }

    window.addEventListener("message", handleMessage);
    return () => window.removeEventListener("message", handleMessage);
  }, [onError, onRender, clearRenderTimeout]);

  // Send render command when dsl changes
  useEffect(() => {
    if (!dsl || !iframeRef.current || status !== "ready") {
      return;
    }

    // Validate DSL against denylist
    const validationError = validateDsl(dsl);
    if (validationError) {
      setStatus("error");
      setErrorMessage(validationError);
      onError?.(validationError);
      return;
    }

    // Clear any previous error
    setErrorMessage("");
    setStatus("loading");

    // Set render timeout
    renderTimeoutRef.current = setTimeout(() => {
      // Timeout - reset iframe
      resetIframe();
      const timeoutError = "Render timeout - iframe reset";
      setErrorMessage(timeoutError);
      onError?.(timeoutError);
    }, RENDER_TIMEOUT_MS);

    // Send render command to iframe
    iframeRef.current.contentWindow?.postMessage(
      { type: "render", payload: dsl },
      "*"
    );

    return () => clearRenderTimeout();
  }, [dsl, status, onError, onRender, clearRenderTimeout, resetIframe]);

  // Clear iframe when dsl is empty
  useEffect(() => {
    if (!dsl && iframeRef.current && status === "ready") {
      iframeRef.current.contentWindow?.postMessage({ type: "clear" }, "*");
    }
  }, [dsl, status]);

  // Generate blob URL for iframe content
  const iframeSrc = useRef<string>("");
  if (!iframeSrc.current) {
    const blob = new Blob([generateIframeHtml()], { type: "text/html" });
    iframeSrc.current = URL.createObjectURL(blob);
  }

  // Cleanup blob URL on unmount
  useEffect(() => {
    return () => {
      if (iframeSrc.current) {
        URL.revokeObjectURL(iframeSrc.current);
        iframeSrc.current = "";
      }
    };
  }, []);

  const containerStyle: React.CSSProperties = {
    width: typeof width === "number" ? `${width}px` : width,
    height: typeof height === "number" ? `${height}px` : height,
    border: "1px solid var(--color-border)",
    borderRadius: "8px",
    overflow: "hidden",
    position: "relative",
    backgroundColor: "var(--color-surface)",
  };

  return (
    <div style={containerStyle} data-testid="graph-sandbox">
      {status === "loading" && (
        <div
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            backgroundColor: "var(--color-overlay)",
            zIndex: 10,
          }}
        >
          <div style={{ color: "var(--color-text-muted)" }}>Rendering graph...</div>
        </div>
      )}

      {status === "error" && errorMessage && (
        <div
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            right: 0,
            padding: "12px 16px",
            backgroundColor: "var(--color-danger-bg)",
            borderBottom: "1px solid var(--color-danger-border)",
            zIndex: 10,
          }}
        >
          <div style={{ color: "var(--color-text-danger)", fontSize: "14px", fontWeight: 500 }}>
            Rendering Error
          </div>
          <div style={{ color: "var(--color-text-danger-secondary)", fontSize: "12px", marginTop: "4px" }}>
            {errorMessage}
          </div>
          <button
            onClick={resetIframe}
            style={{
              marginTop: "8px",
              padding: "4px 12px",
              fontSize: "12px",
              backgroundColor: "var(--color-danger)",
              color: "#ffffff",
              border: "none",
              borderRadius: "4px",
              cursor: "pointer",
            }}
          >
            Reset Renderer
          </button>
        </div>
      )}

      <iframe
        key={iframeKey}
        ref={iframeRef}
        src={iframeSrc.current}
        sandbox="allow-scripts"
        style={{
          width: "100%",
          height: "100%",
          border: "none",
        }}
        title="JSXGraph Sandbox"
        data-testid="jsxgraph-iframe"
      />
    </div>
  );
}

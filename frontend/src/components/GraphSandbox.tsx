import { useCallback, useEffect, useRef, useState } from "react";

// Timeout for rendering operations (30 seconds)
const RENDER_TIMEOUT_MS = 30000;

import { JSXGRAPH_VERSION, JSXGRAPH_CDN_URL, generateIframeHtml } from "./GraphSandbox.iframe";

export { JSXGRAPH_VERSION, JSXGRAPH_CDN_URL, generateIframeHtml };

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
 * Validates JSXGraph DSL with lightweight checks.
 * Returns null if valid, error message if invalid.
 */
export function validateDsl(dsl: string): string | null {
  const stripped = dsl.trim();
  if (!stripped) {
    return null;
  }
  if (stripped.length > 5000) {
    return "DSL is too long";
  }
  return null;
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
 * - Restrictive Content Security Policy (CSP) blocking all network requests and unsafe operations
 * - Pinned JSXGraph version from trusted CDN
 * - Lightweight DSL size validation
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
  // Track whether the iframe is ready to receive commands (separate from render status)
  const iframeReadyRef = useRef(false);
  // Track the last DSL sent to the iframe to avoid redundant renders
  const lastSentDslRef = useRef<string>("");

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
    // Reset iframe readiness tracking
    iframeReadyRef.current = false;
    lastSentDslRef.current = "";
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
          iframeReadyRef.current = true;
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

  // Send render command when dsl changes or iframe becomes ready
  useEffect(() => {
    // Only send render if iframe is ready and we have a DSL to render
    if (!dsl || !iframeRef.current || !iframeReadyRef.current) {
      return;
    }

    // Skip if we've already sent this exact DSL (prevents render loops)
    if (lastSentDslRef.current === dsl) {
      return;
    }

    // Validate DSL
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

    // Track the DSL we're about to send
    lastSentDslRef.current = dsl;

    // Send render command to iframe
    iframeRef.current.contentWindow?.postMessage(
      { type: "render", payload: dsl },
      "*"
    );

    return () => clearRenderTimeout();
  }, [dsl, status, onError, onRender, clearRenderTimeout, resetIframe]);

  // Clear iframe when dsl is empty
  useEffect(() => {
    if (!dsl && iframeRef.current && iframeReadyRef.current) {
      iframeRef.current.contentWindow?.postMessage({ type: "clear" }, "*");
      lastSentDslRef.current = "";
    }
  }, [dsl]);

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

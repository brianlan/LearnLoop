import { useCallback, useEffect, useRef, useState } from "react";

const ALLOWED_ELEMENT_TYPES = new Set([
  "point",
  "segment",
  "line",
  "arrow",
  "circle",
  "angle",
  "polygon",
  "text",
  "glider",
  "intersection",
  "midpoint",
  "perpendicular",
]);

const ALLOWED_OPTION_KEYS = new Set([
  "anchorX",
  "anchorY",
  "color",
  "dash",
  "face",
  "fillColor",
  "fillOpacity",
  "fixed",
  "fontSize",
  "highlight",
  "label",
  "name",
  "opacity",
  "radius",
  "showInfobox",
  "size",
  "strokeColor",
  "strokeOpacity",
  "strokeWidth",
  "visible",
  "withLabel",
]);

const BLOCKED_TOKENS = [
  "constructor",
  "document",
  "eval",
  "fetch",
  "for",
  "function",
  "globalThis",
  "if",
  "import",
  "localStorage",
  "new",
  "prototype",
  "return",
  "sessionStorage",
  "setInterval",
  "setTimeout",
  "this",
  "while",
  "window",
  "XMLHttpRequest",
  "__proto__",
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

function stripQuotedStrings(value: string): string {
  return value.replace(/'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"/g, "");
}

function splitTopLevel(value: string, delimiter: string): string[] | null {
  const parts: string[] = [];
  let start = 0;
  let depth = 0;
  let quote: string | null = null;
  let escaped = false;

  for (let i = 0; i < value.length; i += 1) {
    const char = value[i];
    if (quote) {
      if (escaped) {
        escaped = false;
      } else if (char === "\\") {
        escaped = true;
      } else if (char === quote) {
        quote = null;
      }
      continue;
    }

    if (char === "'" || char === '"') {
      quote = char;
    } else if (char === "[" || char === "{" || char === "(") {
      depth += 1;
    } else if (char === "]" || char === "}" || char === ")") {
      depth -= 1;
      if (depth < 0) {
        return null;
      }
    } else if (char === delimiter && depth === 0) {
      parts.push(value.slice(start, i).trim());
      start = i + 1;
    }
  }

  if (quote || depth !== 0) {
    return null;
  }

  const last = value.slice(start).trim();
  if (last) {
    parts.push(last);
  }
  return parts;
}

function splitStatements(dsl: string): string[] | null {
  const statements = splitTopLevel(dsl, ";");
  if (!statements) {
    return null;
  }
  return statements.filter(Boolean);
}

function isStringLiteral(value: string): boolean {
  return /^'(?:\\.|[^'\\])*'$|^"(?:\\.|[^"\\])*"$/.test(value);
}

function unquote(value: string): string {
  return value.slice(1, -1);
}

function validateObjectLiteral(
  value: string,
  declaredNames: Set<string>,
): string | null {
  if (!value.startsWith("{") || !value.endsWith("}")) {
    return "Expected an object literal";
  }

  const inner = value.slice(1, -1).trim();
  if (!inner) {
    return null;
  }

  const entries = splitTopLevel(inner, ",");
  if (!entries) {
    return "Object literal has unbalanced syntax";
  }

  for (const entry of entries) {
    const keyValue = splitTopLevel(entry, ":");
    if (!keyValue || keyValue.length !== 2) {
      return "Object literal entries must be key-value pairs";
    }
    const key = keyValue[0].trim().replace(/^['"]|['"]$/g, "");
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key) || !ALLOWED_OPTION_KEYS.has(key)) {
      return `Unsupported option key: ${key}`;
    }
    const valueError = validateDslValue(keyValue[1], declaredNames);
    if (valueError) {
      return valueError;
    }
  }

  return null;
}

function validateDslValue(value: string, declaredNames: Set<string>): string | null {
  const trimmed = value.trim();
  if (!trimmed) {
    return "Empty DSL value";
  }
  if (/^-?\d+(?:\.\d+)?$/.test(trimmed)) {
    return null;
  }
  if (isStringLiteral(trimmed)) {
    return null;
  }
  if (trimmed === "true" || trimmed === "false" || trimmed === "null") {
    return null;
  }
  if (/^[A-Za-z_][A-Za-z0-9_]*$/.test(trimmed)) {
    return declaredNames.has(trimmed) ? null : `Unknown identifier: ${trimmed}`;
  }
  if (trimmed.startsWith("[") && trimmed.endsWith("]")) {
    const inner = trimmed.slice(1, -1).trim();
    if (!inner) {
      return null;
    }
    const items = splitTopLevel(inner, ",");
    if (!items) {
      return "Array literal has unbalanced syntax";
    }
    for (const item of items) {
      const itemError = validateDslValue(item, declaredNames);
      if (itemError) {
        return itemError;
      }
    }
    return null;
  }
  if (trimmed.startsWith("{") && trimmed.endsWith("}")) {
    return validateObjectLiteral(trimmed, declaredNames);
  }
  return `Unsupported DSL value: ${trimmed}`;
}

function validateCreateCall(call: string, declaredNames: Set<string>): string | null {
  if (!call.startsWith("board.create(") || !call.endsWith(")")) {
    return "Only board.create(...) calls are allowed";
  }

  const args = splitTopLevel(call.slice("board.create(".length, -1), ",");
  if (!args || args.length < 2 || args.length > 3) {
    return "board.create requires two or three arguments";
  }

  const typeArg = args[0].trim();
  if (!isStringLiteral(typeArg)) {
    return "board.create element type must be a string literal";
  }
  const elementType = unquote(typeArg);
  if (!ALLOWED_ELEMENT_TYPES.has(elementType)) {
    return `Unsupported JSXGraph element type: ${elementType}`;
  }

  const parentsError = validateDslValue(args[1], declaredNames);
  if (parentsError) {
    return parentsError;
  }

  if (args[2]) {
    return validateObjectLiteral(args[2].trim(), declaredNames);
  }

  return null;
}

function validateStatement(statement: string, declaredNames: Set<string>): string | null {
  const trimmed = statement.trim();
  const boundingBoxMatch = trimmed.match(/^board\.setBoundingBox\((.*)\)$/);
  if (boundingBoxMatch) {
    const value = boundingBoxMatch[1].trim();
    const parts = value.startsWith("[") && value.endsWith("]")
      ? splitTopLevel(value.slice(1, -1), ",")
      : null;
    if (!parts || parts.length !== 4 || parts.some((part) => !/^-?\d+(?:\.\d+)?$/.test(part.trim()))) {
      return "board.setBoundingBox must use four numeric literals";
    }
    return null;
  }

  const declarationMatch = trimmed.match(/^var\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(board\.create\(.*\))$/);
  if (declarationMatch) {
    const [, name, call] = declarationMatch;
    if (declaredNames.has(name)) {
      return `Duplicate DSL variable: ${name}`;
    }
    const callError = validateCreateCall(call, declaredNames);
    if (callError) {
      return callError;
    }
    declaredNames.add(name);
    return null;
  }

  if (trimmed.startsWith("board.create(")) {
    return validateCreateCall(trimmed, declaredNames);
  }

  return "Only var declarations, board.setBoundingBox, and board.create calls are allowed";
}

/**
 * Validates model-generated JSXGraph DSL against a strict allowlist.
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

  const unquoted = stripQuotedStrings(stripped);
  if (/=>|`|\/\/|\/\*|\*\/|\+\+|--/.test(unquoted)) {
    return "DSL contains unsupported JavaScript syntax";
  }
  for (const token of BLOCKED_TOKENS) {
    const pattern = new RegExp(`\\b${token}\\b`, "i");
    if (pattern.test(unquoted)) {
      return `DSL contains forbidden token: ${token}`;
    }
  }

  const statements = splitStatements(stripped);
  if (!statements) {
    return "DSL has unbalanced statement syntax";
  }

  const declaredNames = new Set<string>();
  for (const statement of statements) {
    const statementError = validateStatement(statement, declaredNames);
    if (statementError) {
      return statementError;
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

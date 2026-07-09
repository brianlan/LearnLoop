import type { Dispatch, SetStateAction } from "react";
import { GraphSandbox } from "@/components/GraphSandbox";

interface WhiteboardPanelProps {
  isWhiteboardCollapsed: boolean;
  setIsWhiteboardCollapsed: Dispatch<SetStateAction<boolean>>;
  dslPages: string[];
  currentPageIndex: number;
  setCurrentPageIndex: Dispatch<SetStateAction<number>>;
  whiteboardError: string | null;
  setWhiteboardError: Dispatch<SetStateAction<string | null>>;
}

export function WhiteboardPanel({
  isWhiteboardCollapsed,
  setIsWhiteboardCollapsed,
  dslPages,
  currentPageIndex,
  setCurrentPageIndex,
  whiteboardError,
  setWhiteboardError,
}: WhiteboardPanelProps) {
  return isWhiteboardCollapsed ? (
    <div
      data-testid="whiteboard"
      style={{
        backgroundColor: "var(--color-surface)",
        borderRadius: "var(--radius-lg)",
        border: "1px solid var(--color-border)",
        padding: "0.75rem 0.5rem",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "flex-start",
        gap: "0.75rem",
        minHeight: "650px",
      }}
    >
      <button
        onClick={() => setIsWhiteboardCollapsed(false)}
        data-testid="whiteboard-expand"
        aria-label="Expand whiteboard"
        className="btn btn-secondary"
        style={{
          padding: "0.4rem",
          borderRadius: "var(--radius-md)",
          lineHeight: 1,
        }}
      >
        ◀
      </button>
      <span style={{ writingMode: "vertical-rl", fontSize: "0.75rem", fontWeight: 700, color: "var(--color-text-muted)", letterSpacing: "0.05em", textTransform: "uppercase", marginTop: "0.5rem" }}>
        🎨 Whiteboard
      </span>
    </div>
  ) : (
    <div
      data-testid="whiteboard"
      className="card-premium"
      style={{
        backgroundColor: "var(--color-surface)",
        borderRadius: "var(--radius-lg)",
        border: "1px solid var(--color-border)",
        padding: "1.5rem",
        display: "flex",
        flexDirection: "column",
        gap: "1.25rem",
        alignItems: "stretch",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", borderBottom: "1px solid var(--color-border)", paddingBottom: "0.75rem" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
          <span style={{ fontSize: "1.25rem" }}>🎨</span>
          <strong style={{ fontSize: "1rem", color: "var(--color-text)", fontWeight: 800, letterSpacing: "-0.01em" }}>Interactive Whiteboard</strong>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
          {/* Whiteboard pagination controls */}
          {dslPages.length > 0 && (
            <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
              <button
                onClick={() => {
                  setCurrentPageIndex((prev) => Math.max(0, prev - 1));
                  setWhiteboardError(null);
                }}
                disabled={currentPageIndex === 0}
                data-testid="whiteboard-prev"
                className="btn btn-secondary"
                style={{
                  padding: "0.25rem 0.5rem",
                  borderRadius: "var(--radius-md)",
                  fontSize: "0.8125rem",
                }}
              >
                ◀
              </button>

              <span data-testid="whiteboard-page-indicator" style={{ fontSize: "0.8125rem", fontWeight: 700, color: "var(--color-text-muted)" }}>
                {currentPageIndex + 1} / {dslPages.length}
              </span>

              <button
                onClick={() => {
                  setCurrentPageIndex((prev) => Math.min(dslPages.length - 1, prev + 1));
                  setWhiteboardError(null);
                }}
                disabled={currentPageIndex === dslPages.length - 1}
                data-testid="whiteboard-next"
                className="btn btn-secondary"
                style={{
                  padding: "0.25rem 0.5rem",
                  borderRadius: "var(--radius-md)",
                  fontSize: "0.8125rem",
                }}
              >
                ▶
              </button>
            </div>
          )}

          {/* Collapse button */}
          <button
            onClick={() => setIsWhiteboardCollapsed(true)}
            data-testid="whiteboard-collapse"
            aria-label="Collapse whiteboard"
            className="btn btn-secondary"
            style={{
              padding: "0.25rem 0.5rem",
              borderRadius: "var(--radius-md)",
              fontSize: "0.8125rem",
            }}
          >
            ▶
          </button>
        </div>
      </div>

      {/* Canvas area wrapper */}
      <div style={{ flex: 1, position: "relative", minHeight: "400px" }}>
        {dslPages.length === 0 ?

          /* Whiteboard empty state (FR-30) */
          <div
            data-testid="whiteboard-empty"
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              right: 0,
              bottom: 0,
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              backgroundColor: "var(--color-surface-muted)",
              borderRadius: "var(--radius-lg)",
              border: "2px dashed var(--color-border)",
              color: "var(--color-text-muted)",
              padding: "2rem",
              textAlign: "center",
            }}
          >
            <div style={{ fontSize: "3.5rem", marginBottom: "1rem" }}>✏️</div>
            <strong style={{ fontSize: "1.1rem", fontWeight: 700, color: "var(--color-text)" }}>Whiteboard Canvas Empty</strong>
            <p style={{ margin: "0.5rem 0 0 0", fontSize: "0.875rem", maxWidth: "320px", lineHeight: 1.4, fontWeight: 500 }}>
              No whiteboard drawings have been loaded yet. Ask the coach to generate a drawing or click a shortcut like "Draw"!
            </p>
          </div>
        :

          /* Renders DSL pages with JSXGraph GraphSandbox */
          <div style={{ width: "100%", height: "100%" }}>
            {whiteboardError &&

              /* Custom error indicator shown when JSXGraph fails (FR-31) */
              <div
                data-testid="whiteboard-error"
                className="badge badge-danger"
                style={{
                  position: "absolute",
                  top: 0,
                  left: 0,
                  right: 0,
                  zIndex: 20,
                  padding: "0.75rem 1rem",
                  fontSize: "0.875rem",
                  textTransform: "none",
                  letterSpacing: "normal",
                  fontWeight: 500,
                  boxShadow: "0 4px 12px rgba(0, 0, 0, 0.08)",
                  borderRadius: "var(--radius-md)",
                }}
              >
                <strong>Whiteboard Rendering Error:</strong> {whiteboardError}
              </div>
            }

            <GraphSandbox
              key={`whiteboard-canvas-${currentPageIndex}`}
              dsl={dslPages[currentPageIndex]}
              onError={(error) => setWhiteboardError(error)}
              onRender={() => setWhiteboardError(null)}
              height="100%"
            />
          </div>
        }
      </div>
    </div>
  );
}

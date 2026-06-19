import type { ChangeEvent, ClipboardEvent } from "react";

export interface PasteStepProps {
  onPaste: (event: ClipboardEvent) => void;
  fileInputRef: React.RefObject<HTMLInputElement>;
  onFileSelection: (event: ChangeEvent<HTMLInputElement>) => void;
  error: string;
}

export function PasteStep({ onPaste, fileInputRef, onFileSelection, error }: PasteStepProps) {
  return (
    <div
      style={{
        padding: "4rem 2rem",
        textAlign: "center",
        border: "2px dashed var(--color-border)",
        borderRadius: "var(--radius-lg)",
        backgroundColor: "var(--color-surface-muted)",
        transition: "all 0.2s ease-in-out",
        outline: "none",
      }}
      onPaste={onPaste}
      tabIndex={0}
      role="region"
      aria-label="Paste image area"
      className="card-hover"
    >
      <div style={{ marginBottom: "1.25rem", fontSize: "3rem", filter: "drop-shadow(0 4px 6px rgba(0,0,0,0.15))" }}>📋</div>
      <h3 style={{ margin: "0 0 0.5rem", fontSize: "1.25rem", fontWeight: 700, letterSpacing: "-0.01em" }}>
        Paste an Image
      </h3>
      <p style={{ margin: 0, color: "var(--color-text-muted)", fontSize: "0.875rem", fontWeight: 500 }}>
        Copy an image and paste it here (Ctrl+V or Cmd+V)
      </p>
      <div style={{ marginTop: "1.5rem" }}>
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          className="btn btn-secondary"
          style={{
            padding: "0.5rem 1.25rem",
            fontSize: "0.875rem",
            fontWeight: 600,
          }}
        >
          Choose Image File
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept="image/*"
          onChange={(e) => void onFileSelection(e)}
          style={{ display: "none" }}
        />
      </div>
      {error && (
        <div
          className="badge badge-danger"
          style={{
            marginTop: "1.25rem",
            padding: "0.75rem 1rem",
            fontSize: "0.875rem",
            textTransform: "none",
            letterSpacing: "normal",
            fontWeight: 500,
            display: "inline-block"
          }}
        >
          {error}
        </div>
      )}
    </div>
  );
}

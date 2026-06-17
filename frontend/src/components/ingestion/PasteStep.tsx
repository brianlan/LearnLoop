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
        padding: "48px 32px",
        textAlign: "center",
        border: "2px dashed var(--color-border-muted)",
        borderRadius: "8px",
        backgroundColor: "var(--color-surface-muted)",
      }}
      onPaste={onPaste}
      tabIndex={0}
      role="region"
      aria-label="Paste image area"
    >
      <div style={{ marginBottom: "16px", fontSize: "48px" }}>📋</div>
      <h3 style={{ margin: "0 0 8px", fontSize: "18px", fontWeight: 600 }}>
        Paste an Image
      </h3>
      <p style={{ margin: 0, color: "var(--color-text-muted)", fontSize: "14px" }}>
        Copy an image and paste it here (Ctrl+V or Cmd+V)
      </p>
      <div style={{ marginTop: "16px" }}>
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          style={{
            padding: "8px 16px",
            backgroundColor: "var(--color-surface)",
            color: "var(--color-text)",
            border: "1px solid var(--color-border-muted)",
            borderRadius: "4px",
            cursor: "pointer",
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
          style={{
            marginTop: "16px",
            padding: "12px 16px",
            backgroundColor: "var(--color-danger-bg)",
            borderRadius: "6px",
            color: "var(--color-text-danger)",
            fontSize: "14px",
          }}
        >
          {error}
        </div>
      )}
    </div>
  );
}

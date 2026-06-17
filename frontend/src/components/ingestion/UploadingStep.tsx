export interface UploadingStepProps {
  uploadProgress: number;
}

export function UploadingStep({ uploadProgress }: UploadingStepProps) {
  return (
    <div style={{ padding: "48px 32px", textAlign: "center" }}>
      <div style={{ marginBottom: "16px", fontSize: "48px" }}>⏳</div>
      <h3 style={{ margin: "0 0 8px", fontSize: "18px", fontWeight: 600 }}>
        Uploading...
      </h3>
      <div
        style={{
          width: "100%",
          height: "8px",
          backgroundColor: "var(--color-border)",
          borderRadius: "4px",
          overflow: "hidden",
          marginTop: "16px",
        }}
      >
        <div
          style={{
            width: `${uploadProgress}%`,
            height: "100%",
            backgroundColor: "var(--color-primary)",
            transition: "width 0.3s ease",
          }}
        />
      </div>
      <p style={{ margin: "8px 0 0", color: "var(--color-text-muted)", fontSize: "14px" }}>
        {uploadProgress}%
      </p>
    </div>
  );
}

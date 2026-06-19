export interface UploadingStepProps {
  uploadProgress: number;
}

export function UploadingStep({ uploadProgress }: UploadingStepProps) {
  return (
    <div style={{ padding: "4rem 2rem", textAlign: "center" }}>
      <div style={{ marginBottom: "1.25rem", fontSize: "3rem", animation: "spin 2s linear infinite" }}>⏳</div>
      <h3 style={{ margin: "0 0 0.5rem", fontSize: "1.25rem", fontWeight: 700 }}>
        Uploading...
      </h3>
      <div
        style={{
          width: "100%",
          maxWidth: "320px",
          height: "6px",
          backgroundColor: "var(--color-border)",
          borderRadius: "var(--radius-full)",
          overflow: "hidden",
          margin: "1.5rem auto 0",
        }}
      >
        <div
          style={{
            width: `${uploadProgress}%`,
            height: "100%",
            background: "linear-gradient(90deg, var(--color-link), var(--color-primary))",
            transition: "width 0.3s ease",
            borderRadius: "var(--radius-full)"
          }}
        />
      </div>
      <p style={{ margin: "0.75rem 0 0", color: "var(--color-text-muted)", fontSize: "0.875rem", fontWeight: 600 }}>
        {uploadProgress}%
      </p>
    </div>
  );
}

import { useNavigate } from "react-router-dom";
import { BulkIngestionWizard } from "@/components/BulkIngestionWizard";

export function IngestPage() {
  const navigate = useNavigate();

  const handleComplete = () => {
    navigate("/problems");
  };

  const handleCancel = () => {
    navigate("/problems");
  };

  return (
    <main style={{ padding: "24px", minHeight: "100vh", backgroundColor: "var(--color-surface-muted)" }}>
      <h1
        style={{
          margin: "0 0 24px",
          fontSize: "24px",
          fontWeight: 700,
          color: "var(--color-text)",
          textAlign: "center",
        }}
      >
        Ingest New Problems
      </h1>
      <BulkIngestionWizard onComplete={handleComplete} onCancel={handleCancel} />
    </main>
  );
}

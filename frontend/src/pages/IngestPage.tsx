import { useNavigate } from "react-router-dom";
import { IngestionWizard } from "@/components/IngestionWizard";

export function IngestPage() {
  const navigate = useNavigate();

  const handleConfirm = (problemId: string) => {
    navigate(`/problems/${problemId}`);
  };

  const handleCancel = () => {
    navigate("/problems");
  };

  return (
    <main style={{ padding: "24px", minHeight: "100vh", backgroundColor: "#f9fafb" }}>
      <h1
        style={{
          margin: "0 0 24px",
          fontSize: "24px",
          fontWeight: 700,
          color: "#111827",
          textAlign: "center",
        }}
      >
        Ingest New Problem
      </h1>
      <IngestionWizard onConfirm={handleConfirm} onCancel={handleCancel} />
    </main>
  );
}

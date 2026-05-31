import { useState, useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";

import { api } from "@/api/client";
import { Modal } from "@/components/Modal";

interface SettingsResponse {
  app: {
    env: string;
    host: string;
    port: number;
    log_level: string;
  };
  database: {
    name: string;
  };
  storage: {
    endpoint: string;
    bucket: string;
    region: string;
    force_path_style: boolean;
  };
  ingestion_vlm: {
    endpoint: string;
    model: string;
    timeout_seconds: number;
    preview_extracting_window_seconds: number;
  };
  grading_vlm: {
    endpoint: string;
    model: string;
    timeout_seconds: number;
  };
  solution_llm: {
    endpoint: string;
    model: string;
    timeout_seconds: number;
  };
  coaching_llm: {
    endpoint: string;
    model: string;
    timeout_seconds: number;
  };
  session: {
    cookie_name: string;
    secure: boolean;
    samesite: string;
  };
  practice: {
    cooldown_days: number;
    last_wrong_weight: number;
    failure_rate_weight: number;
    recency_weight: number;
  };
}

function SettingRow({ label, value }: { label: string; value: unknown }) {
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        padding: "0.5rem 0",
        borderBottom: "1px solid var(--color-border)",
      }}
    >
      <span style={{ fontWeight: 500, color: "var(--color-text)" }}>{label}</span>
      <span style={{ color: "var(--color-text-muted)", fontFamily: "monospace" }}>
        {String(value)}
      </span>
    </div>
  );
}

function SettingSection({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section style={{ marginBottom: "1.5rem" }}>
      <h2
        style={{
          fontSize: "1.125rem",
          fontWeight: 600,
          marginBottom: "0.75rem",
          color: "var(--color-text)",
          borderBottom: "2px solid var(--color-primary)",
          paddingBottom: "0.25rem",
        }}
      >
        {title}
      </h2>
      <div>{children}</div>
    </section>
  );
}

function ChangeTeacherPasswordModal({
  isOpen,
  onClose,
  onSuccess,
}: {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: () => void;
}) {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const currentRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isOpen) {
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      setError(null);
      setIsSubmitting(false);
      currentRef.current?.focus();
    }
  }, [isOpen]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    if (!currentPassword.trim() || !newPassword.trim() || !confirmPassword.trim()) {
      setError("All fields are required");
      return;
    }

    if (newPassword !== confirmPassword) {
      setError("New passwords do not match");
      return;
    }

    setIsSubmitting(true);
    try {
      await api.changeTeacherPassword(currentPassword, newPassword, confirmPassword);
      onSuccess();
      onClose();
    } catch (err) {
      if (err instanceof Error) {
        if (err.message.includes("Incorrect teacher password")) {
          setError("Incorrect current password");
        } else if (err.message.includes("already set")) {
          setError("Teacher password is not set. Please set it first.");
        } else {
          setError("Failed to change password. Please try again.");
        }
      } else {
        setError("Failed to change password. Please try again.");
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      zIndex={1000}
      overlayTestId="change-password-modal"
      ariaLabelledby="change-password-title"
    >
      <h2 id="change-password-title" style={{ marginTop: 0 }}>
        Change Teacher Password
      </h2>
      <form onSubmit={handleSubmit}>
        <div style={{ marginBottom: "1rem" }}>
          <label htmlFor="current-password" style={{ display: "block", marginBottom: "0.25rem" }}>
            Current Password
          </label>
          <input
            ref={currentRef}
            id="current-password"
            type="password"
            value={currentPassword}
            onChange={(e) => setCurrentPassword(e.target.value)}
            disabled={isSubmitting}
            style={{
              width: "100%",
              padding: "0.5rem",
              border: "1px solid var(--color-border)",
              borderRadius: "4px",
            }}
            data-testid="current-password-input"
          />
        </div>
        <div style={{ marginBottom: "1rem" }}>
          <label htmlFor="new-password" style={{ display: "block", marginBottom: "0.25rem" }}>
            New Password
          </label>
          <input
            id="new-password"
            type="password"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            disabled={isSubmitting}
            style={{
              width: "100%",
              padding: "0.5rem",
              border: "1px solid var(--color-border)",
              borderRadius: "4px",
            }}
            data-testid="new-password-input"
          />
        </div>
        <div style={{ marginBottom: "1rem" }}>
          <label htmlFor="confirm-password" style={{ display: "block", marginBottom: "0.25rem" }}>
            Confirm New Password
          </label>
          <input
            id="confirm-password"
            type="password"
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            disabled={isSubmitting}
            style={{
              width: "100%",
              padding: "0.5rem",
              border: "1px solid var(--color-border)",
              borderRadius: "4px",
            }}
            data-testid="confirm-password-input"
          />
        </div>
        {error && (
          <div
            style={{
              color: "var(--color-text-danger)",
              marginBottom: "1rem",
              fontSize: "0.875rem",
            }}
            role="alert"
            data-testid="change-password-error"
          >
            {error}
          </div>
        )}
        <div style={{ display: "flex", gap: "0.5rem", justifyContent: "flex-end" }}>
          <button
            type="button"
            onClick={onClose}
            disabled={isSubmitting}
            data-testid="change-password-cancel"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={isSubmitting}
            data-testid="change-password-submit"
          >
            {isSubmitting ? "Changing..." : "Change Password"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

export function SettingsPage() {
  const [showChangePasswordModal, setShowChangePasswordModal] = useState(false);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["settings"],
    queryFn: async () => api.get<SettingsResponse>("/settings"),
  });

  if (isLoading) {
    return (
      <main style={{ padding: "1rem", maxWidth: "800px", margin: "0 auto" }}>
        <h1 style={{ marginBottom: "1rem" }}>Settings</h1>
        <p style={{ color: "var(--color-text-muted)" }}>Loading settings...</p>
      </main>
    );
  }

  if (error || !data) {
    return (
      <main style={{ padding: "1rem", maxWidth: "800px", margin: "0 auto" }}>
        <h1 style={{ marginBottom: "1rem" }}>Settings</h1>
        <p style={{ color: "var(--color-text-danger)" }}>Failed to load settings.</p>
      </main>
    );
  }

  return (
    <main style={{ padding: "1rem", maxWidth: "800px", margin: "0 auto" }}>
      <h1 style={{ marginBottom: "1.5rem" }}>Settings</h1>
      <p
        style={{
          color: "var(--color-text-muted)",
          marginBottom: "1.5rem",
          fontStyle: "italic",
        }}
      >
        These are the effective runtime settings of the application (read-only).
      </p>

      {successMessage && (
        <div
          style={{
            backgroundColor: "var(--color-success-bg)",
            color: "var(--color-success-text)",
            padding: "0.75rem 1rem",
            borderRadius: "4px",
            marginBottom: "1rem",
          }}
          role="alert"
          data-testid="success-message"
        >
          {successMessage}
        </div>
      )}

      <SettingSection title="Teacher Password">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span style={{ color: "var(--color-text)" }}>
            Change the password used to access teacher features
          </span>
          <button
            onClick={() => setShowChangePasswordModal(true)}
            data-testid="change-teacher-password-button"
          >
            Change Teacher Password
          </button>
        </div>
      </SettingSection>

      <SettingSection title="Application">
        <SettingRow label="Environment" value={data.app.env} />
        <SettingRow label="Host" value={data.app.host} />
        <SettingRow label="Port" value={data.app.port} />
        <SettingRow label="Log Level" value={data.app.log_level} />
      </SettingSection>

      <SettingSection title="Database">
        <SettingRow label="Database Name" value={data.database.name} />
      </SettingSection>

      <SettingSection title="Storage (S3)">
        <SettingRow label="Endpoint" value={data.storage.endpoint} />
        <SettingRow label="Bucket" value={data.storage.bucket} />
        <SettingRow label="Region" value={data.storage.region} />
        <SettingRow
          label="Force Path Style"
          value={data.storage.force_path_style.toString()}
        />
      </SettingSection>

      <SettingSection title="Ingestion VLM">
        <SettingRow label="Endpoint" value={data.ingestion_vlm.endpoint} />
        <SettingRow label="Model" value={data.ingestion_vlm.model} />
        <SettingRow
          label="Timeout (seconds)"
          value={data.ingestion_vlm.timeout_seconds}
        />
        <SettingRow
          label="Preview Window (seconds)"
          value={data.ingestion_vlm.preview_extracting_window_seconds}
        />
      </SettingSection>

      <SettingSection title="Grading VLM">
        <SettingRow label="Endpoint" value={data.grading_vlm.endpoint} />
        <SettingRow label="Model" value={data.grading_vlm.model} />
        <SettingRow
          label="Timeout (seconds)"
          value={data.grading_vlm.timeout_seconds}
        />
      </SettingSection>

      <SettingSection title="Solution LLM">
        <SettingRow label="Endpoint" value={data.solution_llm.endpoint} />
        <SettingRow label="Model" value={data.solution_llm.model} />
        <SettingRow
          label="Timeout (seconds)"
          value={data.solution_llm.timeout_seconds}
        />
      </SettingSection>

      <SettingSection title="Coaching LLM">
        <SettingRow label="Endpoint" value={data.coaching_llm.endpoint} />
        <SettingRow label="Model" value={data.coaching_llm.model} />
        <SettingRow
          label="Timeout (seconds)"
          value={data.coaching_llm.timeout_seconds}
        />
      </SettingSection>

      <SettingSection title="Session">
        <SettingRow label="Cookie Name" value={data.session.cookie_name} />
        <SettingRow label="Secure" value={data.session.secure.toString()} />
        <SettingRow label="SameSite" value={data.session.samesite} />
      </SettingSection>

      <SettingSection title="Practice Mode">
        <SettingRow label="Cooldown Days" value={data.practice.cooldown_days} />
        <SettingRow
          label="Last Wrong Weight"
          value={data.practice.last_wrong_weight}
        />
        <SettingRow
          label="Failure Rate Weight"
          value={data.practice.failure_rate_weight}
        />
        <SettingRow label="Recency Weight" value={data.practice.recency_weight} />
      </SettingSection>

      <ChangeTeacherPasswordModal
        isOpen={showChangePasswordModal}
        onClose={() => setShowChangePasswordModal(false)}
        onSuccess={() => {
          setSuccessMessage("Teacher password changed successfully");
          setTimeout(() => setSuccessMessage(null), 5000);
        }}
      />
    </main>
  );
}

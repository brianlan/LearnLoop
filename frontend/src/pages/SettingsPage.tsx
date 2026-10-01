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
  preview_extracting_window_seconds: number;
  helper_vlm: VlmProfileSettings;
  math_ingestion_vlm: VlmProfileSettings;
  english_ingestion_vlm: VlmProfileSettings;
  grading_vlm: VlmProfileSettings;
  math_solution_vlm: VlmProfileSettings;
  english_solution_vlm: VlmProfileSettings;
  math_coaching_vlm: VlmProfileSettings;
  english_coaching_vlm: VlmProfileSettings;
  variant_generator_vlm: VlmProfileSettings;
  variant_validator_vlm: VlmProfileSettings;
  variant_validator2_vlm: VlmProfileSettings;
  session: {
    cookie_name: string;
    secure: boolean;
    samesite: string;
  };
  problem_selection: {
    cooldown_days: number;
    last_wrong_weight: number;
    failure_rate_weight: number;
    recency_weight: number;
    min_problem_age_days: number;
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

interface VlmProfileSettings {
  endpoint: string;
  model: string;
  provider: string;
  api_mode: string;
  timeout_seconds: number;
  status: string;
}

interface VlmHealthEntry {
  status: string;
  checked_at?: string | null;
  reason?: string;
  code?: string;
  attempts?: number;
}

interface VlmHealthResponse {
  running: boolean;
  started_at: string | null;
  finished_at: string | null;
  profiles: Record<string, VlmHealthEntry>;
}

function VlmStatusBadge({ status }: { status: string }) {
  if (status === "configured") {
    return (
      <span style={{ color: "var(--color-success, #16a34a)", fontWeight: 600 }}>
        configured
      </span>
    );
  }
  if (status === "misconfigured") {
    return (
      <div>
        <span style={{ color: "var(--color-error, #dc2626)", fontWeight: 600 }}>
          misconfigured
        </span>
        <div style={{ fontSize: "0.8rem", color: "var(--color-text-muted)" }}>
          misconfigured: check env var names — the affected feature is disabled
        </div>
      </div>
    );
  }
  return (
    <div>
      <span style={{ color: "var(--color-text-muted)", fontWeight: 600 }}>
        unconfigured
      </span>
      <div style={{ fontSize: "0.8rem", color: "var(--color-text-muted)" }}>
        unconfigured (optional is normal): the feature falls back to defaults
        or is disabled
      </div>
    </div>
  );
}

function VlmHealthBadge({ entry }: { entry?: VlmHealthEntry }) {
  if (!entry) {
    // Row only renders while a run is in flight and this profile has no
    // entry yet.
    return (
      <span style={{ color: "var(--color-text-muted)", fontWeight: 600 }}>
        checking…
      </span>
    );
  }
  if (entry.status === "ok") {
    return (
      <span style={{ color: "var(--color-success, #16a34a)", fontWeight: 600 }}>
        OK
      </span>
    );
  }
  if (entry.status === "unavailable") {
    return (
      <div>
        <span style={{ color: "var(--color-error, #dc2626)", fontWeight: 600 }}>
          UNAVAILABLE
        </span>
        {entry.reason && (
          <div style={{ fontSize: "0.8rem", color: "var(--color-text-muted)" }}>
            {entry.reason}
          </div>
        )}
      </div>
    );
  }
  // unconfigured / misconfigured: reported without probing.
  return (
    <div>
      <span style={{ color: "var(--color-text-muted)", fontWeight: 600 }}>
        {entry.status.toUpperCase()}
      </span>
      <div style={{ fontSize: "0.8rem", color: "var(--color-text-muted)" }}>
        not probed: profile is not fully configured
      </div>
    </div>
  );
}

function VlmSection({
  title,
  vlm,
  health,
  healthRunning,
}: {
  title: string;
  vlm: VlmProfileSettings;
  health?: VlmHealthEntry;
  healthRunning: boolean;
}) {
  const showHealth = health !== undefined || healthRunning;
  return (
    <SettingSection title={title}>
      <SettingRow label="Endpoint" value={vlm.endpoint} />
      <SettingRow label="Model" value={vlm.model} />
      <SettingRow label="Provider" value={vlm.provider} />
      <SettingRow label="API mode" value={vlm.api_mode} />
      <SettingRow label="Timeout (seconds)" value={vlm.timeout_seconds} />
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          padding: "0.5rem 0",
        }}
      >
        <span style={{ fontWeight: 500, color: "var(--color-text)" }}>Status</span>
        <VlmStatusBadge status={vlm.status} />
      </div>
      {showHealth && (
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            padding: "0.5rem 0",
            borderBottom: "1px solid var(--color-border)",
          }}
        >
          <span style={{ fontWeight: 500, color: "var(--color-text)" }}>
            Health
          </span>
          <VlmHealthBadge entry={health} />
        </div>
      )}
    </SettingSection>
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

  const healthQuery = useQuery({
    queryKey: ["vlm-health"],
    queryFn: async () => api.get<VlmHealthResponse>("/settings/vlm-health"),
    refetchInterval: (query) => (query.state.data?.running ? 5000 : false),
  });
  const health = healthQuery.data;

  const rerunHealth = async () => {
    try {
      await api.post("/settings/vlm-health/run", {});
    } catch {
      // 409 while a run is already in flight — the 5s poll picks it up.
    }
    await healthQuery.refetch();
  };

  const pageCanvasStyle: React.CSSProperties = {
    minHeight: "calc(100vh - 60px)",
    backgroundColor: "var(--color-surface-muted)",
    color: "var(--color-text)",
    padding: "1rem",
  };

  const contentWrapperStyle: React.CSSProperties = {
    maxWidth: "800px",
    margin: "0 auto",
  };

  if (isLoading) {
    return (
      <main style={pageCanvasStyle}>
        <div style={contentWrapperStyle}>
          <h1 style={{ marginBottom: "1rem" }}>Settings</h1>
          <p style={{ color: "var(--color-text-muted)" }}>Loading settings...</p>
        </div>
      </main>
    );
  }

  if (error || !data) {
    return (
      <main style={pageCanvasStyle}>
        <div style={contentWrapperStyle}>
          <h1 style={{ marginBottom: "1rem" }}>Settings</h1>
          <p style={{ color: "var(--color-text-danger)" }}>Failed to load settings.</p>
        </div>
      </main>
    );
  }

  return (
    <main style={pageCanvasStyle}>
      <div style={contentWrapperStyle}>
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

      <SettingSection title="Preview">
        <SettingRow label="Preview Window (seconds)" value={data.preview_extracting_window_seconds} />
      </SettingSection>

      {health && (
        <SettingSection title="VLM Health">
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              padding: "0.5rem 0",
            }}
          >
            <span style={{ color: "var(--color-text)" }}>
              {health.finished_at ?? health.started_at
                ? `Last checked: ${new Date(
                    health.finished_at ?? health.started_at!
                  ).toLocaleString()}`
                : health.running
                  ? "Checking…"
                  : "Not checked yet"}
            </span>
            <button
              onClick={rerunHealth}
              disabled={health.running}
              data-testid="vlm-health-rerun"
            >
              {health.running ? "Checking…" : "Re-run"}
            </button>
          </div>
          <div style={{ fontSize: "0.8rem", color: "var(--color-text-muted)" }}>
            Text-only ping; vision-only endpoints may show unavailable while real
            image traffic works.
          </div>
        </SettingSection>
      )}

      <VlmSection title="Helper VLM" vlm={data.helper_vlm} health={health?.profiles.helper_vlm} healthRunning={Boolean(health?.running)} />
      <VlmSection title="Math Ingestion VLM" vlm={data.math_ingestion_vlm} health={health?.profiles.math_ingestion_vlm} healthRunning={Boolean(health?.running)} />
      <VlmSection title="English Ingestion VLM" vlm={data.english_ingestion_vlm} health={health?.profiles.english_ingestion_vlm} healthRunning={Boolean(health?.running)} />
      <VlmSection title="Grading VLM" vlm={data.grading_vlm} health={health?.profiles.grading_vlm} healthRunning={Boolean(health?.running)} />
      <VlmSection title="Math Solution VLM" vlm={data.math_solution_vlm} health={health?.profiles.math_solution_vlm} healthRunning={Boolean(health?.running)} />
      <VlmSection title="English Solution VLM" vlm={data.english_solution_vlm} health={health?.profiles.english_solution_vlm} healthRunning={Boolean(health?.running)} />
      <VlmSection title="Math Coaching VLM" vlm={data.math_coaching_vlm} health={health?.profiles.math_coaching_vlm} healthRunning={Boolean(health?.running)} />
      <VlmSection title="English Coaching VLM" vlm={data.english_coaching_vlm} health={health?.profiles.english_coaching_vlm} healthRunning={Boolean(health?.running)} />
      <VlmSection title="Variant Generator VLM" vlm={data.variant_generator_vlm} health={health?.profiles.variant_generator_vlm} healthRunning={Boolean(health?.running)} />
      <VlmSection title="Variant Validator VLM" vlm={data.variant_validator_vlm} health={health?.profiles.variant_validator_vlm} healthRunning={Boolean(health?.running)} />
      <VlmSection
        title="Variant Validator2 VLM (optional)"
        vlm={data.variant_validator2_vlm}
        health={health?.profiles.variant_validator2_vlm}
        healthRunning={Boolean(health?.running)}
      />

      <SettingSection title="Session">
        <SettingRow label="Cookie Name" value={data.session.cookie_name} />
        <SettingRow label="Secure" value={data.session.secure.toString()} />
        <SettingRow label="SameSite" value={data.session.samesite} />
      </SettingSection>

      <SettingSection title="Problem Selection">
        <SettingRow label="Cooldown Days" value={data.problem_selection.cooldown_days} />
        <SettingRow
          label="Last Wrong Weight"
          value={data.problem_selection.last_wrong_weight}
        />
        <SettingRow
          label="Failure Rate Weight"
          value={data.problem_selection.failure_rate_weight}
        />
        <SettingRow label="Recency Weight" value={data.problem_selection.recency_weight} />
        <SettingRow label="Min Problem Age Days" value={data.problem_selection.min_problem_age_days} />
      </SettingSection>

      <ChangeTeacherPasswordModal
        isOpen={showChangePasswordModal}
        onClose={() => setShowChangePasswordModal(false)}
        onSuccess={() => {
          setSuccessMessage("Teacher password changed successfully");
          setTimeout(() => setSuccessMessage(null), 5000);
        }}
      />
      </div>
    </main>
  );
}

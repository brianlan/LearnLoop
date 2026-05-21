import { useQuery } from "@tanstack/react-query";

import { api } from "@/api/client";

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
  vlm: {
    endpoint: string;
    model: string;
    timeout_seconds: number;
    preview_extracting_window_seconds: number;
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
        borderBottom: "1px solid #e5e7eb",
      }}
    >
      <span style={{ fontWeight: 500, color: "#374151" }}>{label}</span>
      <span style={{ color: "#6b7280", fontFamily: "monospace" }}>
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
          color: "#111827",
          borderBottom: "2px solid #3b82f6",
          paddingBottom: "0.25rem",
        }}
      >
        {title}
      </h2>
      <div>{children}</div>
    </section>
  );
}

export function SettingsPage() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["settings"],
    queryFn: async () => api.get<SettingsResponse>("/settings"),
  });

  if (isLoading) {
    return (
      <main style={{ padding: "1rem", maxWidth: "800px", margin: "0 auto" }}>
        <h1 style={{ marginBottom: "1rem" }}>Settings</h1>
        <p style={{ color: "#6b7280" }}>Loading settings...</p>
      </main>
    );
  }

  if (error || !data) {
    return (
      <main style={{ padding: "1rem", maxWidth: "800px", margin: "0 auto" }}>
        <h1 style={{ marginBottom: "1rem" }}>Settings</h1>
        <p style={{ color: "#dc2626" }}>Failed to load settings.</p>
      </main>
    );
  }

  return (
    <main style={{ padding: "1rem", maxWidth: "800px", margin: "0 auto" }}>
      <h1 style={{ marginBottom: "1.5rem" }}>Settings</h1>
      <p
        style={{
          color: "#6b7280",
          marginBottom: "1.5rem",
          fontStyle: "italic",
        }}
      >
        These are the effective runtime settings of the application (read-only).
      </p>

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

      <SettingSection title="Vision Language Model">
        <SettingRow label="Endpoint" value={data.vlm.endpoint} />
        <SettingRow label="Model" value={data.vlm.model} />
        <SettingRow
          label="Timeout (seconds)"
          value={data.vlm.timeout_seconds}
        />
        <SettingRow
          label="Preview Window (seconds)"
          value={data.vlm.preview_extracting_window_seconds}
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
    </main>
  );
}

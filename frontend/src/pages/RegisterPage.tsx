import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "@/contexts/AuthContext";

export function RegisterPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const { register } = useAuth();
  const navigate = useNavigate();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setIsSubmitting(true);

    try {
      await register(username, password);
      navigate("/login", {
        replace: true,
        state: { registrationSuccess: true, username },
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Registration failed");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div style={{ minHeight: "100vh", display: "flex", flexDirection: "column", justifyContent: "center", backgroundColor: "var(--color-bg)", padding: "1.5rem" }}>
      <main className="card-premium" style={{ maxWidth: "420px", width: "100%", margin: "0 auto", display: "flex", flexDirection: "column", gap: "1.5rem", boxShadow: "var(--shadow-xl)" }}>
        <div style={{ textAlign: "center" }}>
          <strong style={{ fontSize: "1.75rem", fontWeight: 800, letterSpacing: "-0.03em", display: "inline-flex", alignItems: "center", gap: "0.25rem", marginBottom: "0.5rem" }}>
            <span className="text-gradient">Learn</span>
            <span style={{ color: "var(--color-text)" }}>Loop</span>
            <span style={{ width: "6px", height: "6px", backgroundColor: "var(--color-primary)", borderRadius: "50%" }} />
          </strong>
          <h1 style={{ fontSize: "1.5rem", fontWeight: 700, color: "var(--color-text)", margin: 0 }}>Register</h1>
          <p style={{ color: "var(--color-text-muted)", fontSize: "0.875rem", margin: "0.25rem 0 0 0" }}>Create an account to start your learning loop</p>
        </div>

        <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: "1.25rem" }}>
          <div style={{ display: "flex", flexDirection: "column", gap: "0.375rem" }}>
            <label htmlFor="username" style={{ fontSize: "0.75rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--color-text-muted)" }}>Username</label>
            <input
              id="username"
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              disabled={isSubmitting}
              style={{
                width: "100%",
                padding: "0.75rem 1rem",
                borderRadius: "var(--radius-md)",
                border: "1px solid var(--color-border)",
                backgroundColor: "var(--color-surface-muted)"
              }}
            />
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: "0.375rem" }}>
            <label htmlFor="password" style={{ fontSize: "0.75rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--color-text-muted)" }}>Password</label>
            <input
              id="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              disabled={isSubmitting}
              style={{
                width: "100%",
                padding: "0.75rem 1rem",
                borderRadius: "var(--radius-md)",
                border: "1px solid var(--color-border)",
                backgroundColor: "var(--color-surface-muted)"
              }}
            />
          </div>

          {error && (
            <div 
              className="badge badge-danger" 
              style={{ 
                display: "block",
                padding: "0.75rem 1rem",
                borderRadius: "var(--radius-md)",
                fontSize: "0.825rem",
                textTransform: "none",
                fontWeight: 500,
                letterSpacing: "normal"
              }} 
              role="alert"
            >
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={isSubmitting}
            className="btn btn-primary"
            style={{ width: "100%", padding: "0.75rem", borderRadius: "var(--radius-md)", fontSize: "1rem" }}
          >
            {isSubmitting ? "Registering..." : "Register"}
          </button>
        </form>

        <p style={{ margin: 0, textAlign: "center", fontSize: "0.875rem", color: "var(--color-text-muted)" }}>
          Already have an account?{" "}
          <a href="/login" onClick={(e) => { e.preventDefault(); navigate("/login"); }} style={{ fontWeight: 600 }}>
            Login
          </a>
        </p>
      </main>
    </div>
  );
}

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
      navigate("/login");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Registration failed");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <main style={{ maxWidth: "400px", margin: "2rem auto", padding: "1rem" }}>
      <h1>Register</h1>
      <form onSubmit={handleSubmit}>
        <div style={{ marginBottom: "1rem" }}>
          <label htmlFor="username">Username</label>
          <input
            id="username"
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
            disabled={isSubmitting}
            style={{ display: "block", width: "100%", padding: "0.5rem" }}
          />
        </div>
        <div style={{ marginBottom: "1rem" }}>
          <label htmlFor="password">Password</label>
          <input
            id="password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            disabled={isSubmitting}
            style={{ display: "block", width: "100%", padding: "0.5rem" }}
          />
        </div>
        {error && (
          <div style={{ color: "red", marginBottom: "1rem" }} role="alert">
            {error}
          </div>
        )}
        <button
          type="submit"
          disabled={isSubmitting}
          style={{ padding: "0.5rem 1rem" }}
        >
          {isSubmitting ? "Registering..." : "Register"}
        </button>
      </form>
      <p style={{ marginTop: "1rem" }}>
        Already have an account?{" "}
        <a href="/login" onClick={(e) => { e.preventDefault(); navigate("/login"); }}>
          Login
        </a>
      </p>
    </main>
  );
}

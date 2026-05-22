import { useState, useEffect, useRef } from "react";
import { api } from "@/api/client";

interface TeacherPasswordModalProps {
  isOpen: boolean;
  onClose: () => void;
  onVerified: () => void;
}

export function TeacherPasswordModal({
  isOpen,
  onClose,
  onVerified,
}: TeacherPasswordModalProps) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isOpen) {
      setPassword("");
      setError(null);
      setIsSubmitting(false);
      inputRef.current?.focus();
    }
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    if (!password.trim()) {
      setError("Password cannot be empty");
      return;
    }

    setIsSubmitting(true);
    try {
      await api.verifyTeacherPassword(password);
      onVerified();
      onClose();
    } catch (err) {
      if (err instanceof Error) {
        if (err.message.includes("Incorrect teacher password")) {
          setError("Incorrect teacher password");
        } else {
          setError("Network error. Please try again.");
        }
      } else {
        setError("Network error. Please try again.");
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div
      style={{
        position: "fixed",
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        backgroundColor: "rgba(0, 0, 0, 0.5)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 1000,
      }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="teacher-password-title"
      data-testid="teacher-password-modal"
    >
      <div
        style={{
          backgroundColor: "white",
          padding: "1.5rem",
          borderRadius: "8px",
          maxWidth: "400px",
          width: "100%",
        }}
      >
        <h2 id="teacher-password-title" style={{ marginTop: 0 }}>
          Enter Teacher Password
        </h2>
        <form onSubmit={handleSubmit}>
          <div style={{ marginBottom: "1rem" }}>
            <label htmlFor="teacher-password-input" style={{ display: "block", marginBottom: "0.5rem" }}>
              Teacher Password
            </label>
            <input
              ref={inputRef}
              id="teacher-password-input"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={isSubmitting}
              style={{
                width: "100%",
                padding: "0.5rem",
                border: "1px solid #ccc",
                borderRadius: "4px",
              }}
              data-testid="teacher-password-input"
            />
          </div>
          {error && (
            <div
              style={{
                color: "red",
                marginBottom: "1rem",
                fontSize: "0.875rem",
              }}
              role="alert"
              data-testid="teacher-password-error"
            >
              {error}
            </div>
          )}
          <div style={{ display: "flex", gap: "0.5rem", justifyContent: "flex-end" }}>
            <button
              type="button"
              onClick={onClose}
              disabled={isSubmitting}
              data-testid="teacher-password-cancel"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isSubmitting}
              data-testid="teacher-password-submit"
            >
              {isSubmitting ? "Verifying..." : "Submit"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
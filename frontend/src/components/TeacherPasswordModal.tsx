import { useState, useEffect, useRef } from "react";
import { api } from "@/api/client";
import { Modal } from "./Modal";

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
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      zIndex={1000}
      overlayTestId="teacher-password-modal"
      ariaLabelledby="teacher-password-title"
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
              border: "1px solid var(--color-border)",
              borderRadius: "4px",
            }}
            data-testid="teacher-password-input"
          />
        </div>
        {error && (
          <div
            style={{
              color: "var(--color-danger)",
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
    </Modal>
  );
}
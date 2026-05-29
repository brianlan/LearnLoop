import { useEffect } from "react";

interface ModalProps {
  isOpen: boolean;
  onClose: () => void;
  title?: string;
  children: React.ReactNode;
  zIndex?: number;
  overlayTestId?: string;
  ariaLabelledby?: string;
  cardStyle?: React.CSSProperties;
}

export function Modal({
  isOpen,
  onClose,
  title,
  children,
  zIndex,
  overlayTestId,
  ariaLabelledby,
  cardStyle,
}: ModalProps) {
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

  const overlayStyle: React.CSSProperties = {
    position: "fixed",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    backgroundColor: "rgba(0, 0, 0, 0.5)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    ...(zIndex !== undefined ? { zIndex } : {}),
  };

  const defaultCardStyle: React.CSSProperties = {
    backgroundColor: "var(--color-surface)",
    padding: "1.5rem",
    borderRadius: "8px",
    maxWidth: "400px",
    width: "100%",
  };

  const mergedCardStyle = { ...defaultCardStyle, ...cardStyle };

  return (
    <div
      style={overlayStyle}
      role="dialog"
      aria-modal="true"
      aria-labelledby={ariaLabelledby}
      data-testid={overlayTestId}
    >
      <div style={mergedCardStyle}>
        {title && <h2 style={{ marginTop: 0 }}>{title}</h2>}
        {children}
      </div>
    </div>
  );
}

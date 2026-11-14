import React, { useEffect } from "react";

export interface ModalProps {
  open: boolean;
  title: string;
  onClose: () => void;
  children: React.ReactNode;
  footer?: React.ReactNode;
}

export const Modal: React.FC<ModalProps> = ({
  open,
  title,
  onClose,
  children,
  footer,
}) => {
  useEffect(() => {
    if (!open) return;
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="lg-modal-backdrop"
      role="presentation"
      onClick={(e) => {
        if (e.target === e.currentTarget) {
          onClose();
        }
      }}
    >
      <div
        className="lg-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="lg-modal-title"
      >
        <div className="lg-modal__header">
          <h2 id="lg-modal-title" className="lg-modal__title">
            {title}
          </h2>
          <button
            type="button"
            className="lg-icon-btn"
            onClick={onClose}
            aria-label="Close modal"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </button>
        </div>
        <div className="lg-modal__body">{children}</div>
        {footer ? <div className="lg-modal__footer">{footer}</div> : null}
      </div>
    </div>
  );
};

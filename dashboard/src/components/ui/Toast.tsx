import React, { useEffect } from "react";

export type ToastTone = "info" | "success" | "warning" | "danger";

export interface ToastData {
  id: string;
  title: string;
  description?: string;
  tone?: ToastTone;
  durationMs?: number;
}

export interface ToastProps {
  toast: ToastData;
  onDismiss: (id: string) => void;
}

export const Toast: React.FC<ToastProps> = ({ toast, onDismiss }) => {
  useEffect(() => {
    const ms = toast.durationMs ?? 4000;
    if (ms <= 0) return;
    const timer = window.setTimeout(() => onDismiss(toast.id), ms);
    return () => window.clearTimeout(timer);
  }, [toast.id, toast.durationMs, onDismiss]);

  const tone = toast.tone ?? "info";

  return (
    <div
      className={`lg-toast lg-toast--${tone}`}
      role="status"
      aria-live="polite"
    >
      <span className="lg-toast__accent" aria-hidden="true" />
      <div className="lg-toast__content">
        <p className="lg-toast__title">{toast.title}</p>
        {toast.description ? (
          <p className="lg-toast__desc">{toast.description}</p>
        ) : null}
      </div>
      <button
        type="button"
        className="lg-icon-btn"
        onClick={() => onDismiss(toast.id)}
        aria-label="Dismiss notification"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
          <path d="M18 6L6 18M6 6l12 12" />
        </svg>
      </button>
    </div>
  );
};

export interface ToastStackProps {
  toasts: ToastData[];
  onDismiss: (id: string) => void;
}

export const ToastStack: React.FC<ToastStackProps> = ({ toasts, onDismiss }) => {
  if (toasts.length === 0) return null;
  return (
    <div className="lg-toast-stack">
      {toasts.map((item) => (
        <Toast key={item.id} toast={item} onDismiss={onDismiss} />
      ))}
    </div>
  );
};

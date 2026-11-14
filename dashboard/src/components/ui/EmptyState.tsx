import React from "react";

export interface EmptyStateProps {
  title: string;
  description: string;
  icon?: React.ReactNode;
  action?: React.ReactNode;
}

export const EmptyState: React.FC<EmptyStateProps> = ({
  title,
  description,
  icon,
  action,
}) => {
  return (
    <div className="lg-empty">
      <div className="lg-empty__icon" aria-hidden="true">
        {icon ?? (
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="4" width="18" height="14" rx="3" />
            <path d="M8 20h8M12 18v2" />
          </svg>
        )}
      </div>
      <h3 className="lg-empty__title">{title}</h3>
      <p className="lg-empty__desc">{description}</p>
      {action ? <div>{action}</div> : null}
    </div>
  );
};

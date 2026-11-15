import React from "react";
import type { NavItem } from "./index";

interface SideRailProps {
  items: NavItem[];
  activeId: string;
  onSelect: (id: string) => void;
  onLogout?: () => void;
  brandTitle?: string;
}

export const SideRail: React.FC<SideRailProps> = ({
  items,
  activeId,
  onSelect,
  onLogout,
  brandTitle = "Vision AI",
}) => {
  return (
    <aside className="lg-siderail" aria-label="Desktop primary navigation">
      <div className="lg-siderail-header">
        <div className="lg-siderail-brand-icon" aria-hidden="true">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z" />
            <circle cx="12" cy="12" r="3" />
          </svg>
        </div>
        <span className="lg-siderail-brand-title">{brandTitle}</span>
      </div>

      <nav className="lg-siderail-nav" role="navigation">
        <ul className="lg-siderail-list">
          {items.map((item) => {
            const isActive = activeId === item.id;
            return (
              <li key={item.id} className="lg-siderail-item">
                <button
                  type="button"
                  className={`lg-siderail-btn ${isActive ? "active" : ""}`}
                  onClick={() => onSelect(item.id)}
                  aria-current={isActive ? "page" : undefined}
                >
                  <span className="lg-siderail-icon" aria-hidden="true">{item.icon}</span>
                  <span className="lg-siderail-label">{item.label}</span>
                </button>
              </li>
            );
          })}
        </ul>
      </nav>

      {onLogout && (
        <div className="lg-siderail-footer">
          <button
            type="button"
            className="lg-siderail-btn lg-siderail-logout"
            onClick={onLogout}
            title="Sign out"
            aria-label="Sign out"
          >
            <span className="lg-siderail-icon" aria-hidden="true">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
                <polyline points="16 17 21 12 16 7" />
                <line x1="21" y1="12" x2="9" y2="12" />
              </svg>
            </span>
            <span className="lg-siderail-label">Sign out</span>
          </button>
        </div>
      )}
    </aside>
  );
};

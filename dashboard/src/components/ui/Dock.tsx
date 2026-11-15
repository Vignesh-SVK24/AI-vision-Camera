import React from "react";
import type { NavItem } from "./index";

interface DockProps {
  items: NavItem[];
  activeId: string;
  onSelect: (id: string) => void;
}

export const Dock: React.FC<DockProps> = ({ items, activeId, onSelect }) => {
  return (
    <nav className="lg-dock-wrapper" aria-label="Mobile primary navigation">
      <div className="lg-dock" role="navigation">
        {items.map((item) => {
          const isActive = activeId === item.id;
          return (
            <button
              key={item.id}
              type="button"
              className={`lg-dock-btn ${isActive ? "active" : ""}`}
              onClick={() => onSelect(item.id)}
              aria-current={isActive ? "page" : undefined}
            >
              <span className="lg-dock-icon" aria-hidden="true">{item.icon}</span>
              <span className="lg-dock-label">{item.label}</span>
            </button>
          );
        })}
      </div>
    </nav>
  );
};

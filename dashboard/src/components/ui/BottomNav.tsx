import React from "react";
import type { NavItem } from "./NavBar";

export interface BottomNavProps {
  items: NavItem[];
  activeId: string;
  onSelect?: (id: string) => void;
  embedded?: boolean;
  forceVisible?: boolean;
}

export const BottomNav: React.FC<BottomNavProps> = ({
  items,
  activeId,
  onSelect,
  embedded = false,
  forceVisible = false,
}) => {
  const classes = [
    "lg-bottomnav",
    embedded ? "lg-bottomnav--embedded" : "",
    forceVisible ? "lg-bottomnav--force" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <nav className={classes} aria-label="Mobile navigation">
      <div className="lg-bottomnav__list">
        {items.map((item) => {
          const active = item.id === activeId;
          return (
            <button
              key={item.id}
              type="button"
              className={`lg-bottomnav__item ${
                active ? "lg-bottomnav__item--active" : ""
              }`}
              onClick={() => onSelect?.(item.id)}
              aria-current={active ? "page" : undefined}
            >
              {item.icon}
              <span>{item.label}</span>
            </button>
          );
        })}
      </div>
    </nav>
  );
};

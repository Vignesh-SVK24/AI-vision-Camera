import React from "react";

export interface NavItem {
  id: string;
  label: string;
  href?: string;
  icon?: React.ReactNode;
}

export interface NavBarProps {
  title: string;
  logo?: React.ReactNode;
  items: NavItem[];
  activeId: string;
  onSelect?: (id: string) => void;
  rightSlot?: React.ReactNode;
}

export const NavBar: React.FC<NavBarProps> = ({
  title,
  logo,
  items,
  activeId,
  onSelect,
  rightSlot,
}) => {
  return (
    <header className="lg-navbar">
      <div className="lg-navbar__inner">
        <a
          className="lg-navbar__brand"
          href={items[0]?.href ?? "/design"}
          onClick={(e) => {
            if (onSelect && items[0]) {
              e.preventDefault();
              onSelect(items[0].id);
            }
          }}
        >
          <span className="lg-navbar__logo" aria-hidden="true">
            {logo ?? "VS"}
          </span>
          <span>{title}</span>
        </a>

        <nav className="lg-navbar__links" aria-label="Primary navigation">
          {items.map((item) => {
            const active = item.id === activeId;
            return (
              <button
                key={item.id}
                type="button"
                className={`lg-navbar__link ${active ? "lg-navbar__link--active" : ""}`}
                onClick={() => onSelect?.(item.id)}
                aria-current={active ? "page" : undefined}
              >
                {item.icon}
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>

        {rightSlot ? <div className="lg-navbar__actions">{rightSlot}</div> : null}
      </div>
    </header>
  );
};

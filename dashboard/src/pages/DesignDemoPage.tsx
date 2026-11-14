import React, { useCallback, useState } from "react";
import {
  BottomNav,
  Button,
  Chip,
  EmptyState,
  GlassCard,
  Input,
  Modal,
  NavBar,
  Skeleton,
  ToastStack,
  type NavItem,
  type ToastData,
  type ToastTone,
} from "../components/ui";
import "./DesignDemoPage.css";

const NAV_ITEMS: NavItem[] = [
  {
    id: "design",
    label: "Design",
    href: "/design",
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <rect x="3" y="3" width="7" height="7" rx="1.5" />
        <rect x="14" y="3" width="7" height="7" rx="1.5" />
        <rect x="3" y="14" width="7" height="7" rx="1.5" />
        <rect x="14" y="14" width="7" height="7" rx="1.5" />
      </svg>
    ),
  },
  {
    id: "live",
    label: "Live",
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M23 7l-7 5 7 5V7z" />
        <rect x="1" y="5" width="15" height="14" rx="2" />
      </svg>
    ),
  },
  {
    id: "events",
    label: "Events",
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
        <path d="M13.73 21a2 2 0 0 1-3.46 0" />
      </svg>
    ),
  },
  {
    id: "people",
    label: "People",
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
        <circle cx="9" cy="7" r="4" />
      </svg>
    ),
  },
];

const SWATCHES = [
  { name: "Mist Canvas", hex: "#F0F5F7", cssVar: "--color-bg-canvas" },
  { name: "Pine Ink", hex: "#162623", cssVar: "--color-ink-primary" },
  { name: "Deep Teal", hex: "#295255", cssVar: "--color-accent-primary" },
  { name: "Sage Slate", hex: "#577877", cssVar: "--color-accent-muted" },
];

export const DesignDemoPage: React.FC = () => {
  const [activeNav, setActiveNav] = useState("design");
  const [selectedFilter, setSelectedFilter] = useState("all");
  const [modalOpen, setModalOpen] = useState(false);
  const [searchValue, setSearchValue] = useState("");
  const [pinError, setPinError] = useState("Must be at least 8 characters");
  const [toasts, setToasts] = useState<ToastData[]>([]);

  const pushToast = useCallback((tone: ToastTone, title: string, description: string) => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
    setToasts((prev) => [...prev.slice(-2), { id, tone, title, description }]);
  }, []);

  const dismissToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((item) => item.id !== id));
  }, []);

  return (
    <div className="demo-shell">
      <NavBar
        title="Vision Surveillance"
        items={NAV_ITEMS}
        activeId={activeNav}
        onSelect={setActiveNav}
        rightSlot={
          <Button
            variant="secondary"
            size="sm"
            onClick={() =>
              pushToast("info", "Liquid Glass Ready", "System UI tokens loaded.")
            }
          >
            Ping
          </Button>
        }
      />

      <main className="demo-main">
        <GlassCard variant="elevated">
          <div className="demo-hero">
            <div className="demo-row">
              <Chip label="Liquid Glass UI" variant="accent" dot />
              <Chip label="44px Touch Targets" variant="success" dot />
              <Chip label="Safe Area Ready" variant="neutral" />
            </div>
            <h1 className="demo-hero__title">Design System & Component Showcase</h1>
            <p className="demo-hero__subtitle">
              Mobile-first Liquid Glass primitives built on CSS custom properties
              (#F0F5F7, #162623, #295255, #577877), hover glows, pop-up entry motion,
              and prefers-reduced-motion support.
            </p>
          </div>
        </GlassCard>

        <GlassCard>
          <h2 className="demo-section-title">Color Tokens</h2>
          <div className="demo-swatches">
            {SWATCHES.map((sw) => (
              <div key={sw.hex} className="demo-swatch">
                <div
                  className="demo-swatch__color"
                  style={{ backgroundColor: sw.hex }}
                />
                <span className="demo-swatch__name">{sw.name}</span>
                <span className="demo-swatch__hex">
                  {sw.hex} · var({sw.cssVar})
                </span>
              </div>
            ))}
          </div>
        </GlassCard>

        <div className="demo-grid demo-grid--2">
          <GlassCard>
            <h2 className="demo-section-title">Buttons (44px Min Target)</h2>
            <div className="demo-stack">
              <div className="demo-row">
                <Button variant="primary">Primary Action</Button>
                <Button variant="secondary">Secondary</Button>
                <Button variant="primary" loading>
                  Saving
                </Button>
                <Button variant="secondary" disabled>
                  Disabled
                </Button>
              </div>
              <Button variant="primary" fullWidth onClick={() => setModalOpen(true)}>
                Open Glass Modal
              </Button>
            </div>
          </GlassCard>

          <GlassCard>
            <h2 className="demo-section-title">Chips & Interactive Filters</h2>
            <div className="demo-stack">
              <div className="demo-row">
                <Chip label="Camera Online" variant="success" dot />
                <Chip label="Motion Active" variant="accent" dot />
                <Chip label="Queue Retrying" variant="warning" dot />
                <Chip label="Zone Intrusion" variant="danger" dot />
              </div>
              <div className="demo-row">
                {["all", "unknown", "zone", "system"].map((filter) => (
                  <Chip
                    key={filter}
                    label={filter.charAt(0).toUpperCase() + filter.slice(1)}
                    selected={selectedFilter === filter}
                    onClick={() => setSelectedFilter(filter)}
                  />
                ))}
              </div>
            </div>
          </GlassCard>
        </div>

        <div className="demo-grid demo-grid--2">
          <GlassCard>
            <h2 className="demo-section-title">Inputs & Validation</h2>
            <div className="demo-stack">
              <Input
                label="Search Events"
                placeholder="Filter by person or zone..."
                value={searchValue}
                onChange={(e) => setSearchValue(e.target.value)}
                hint="16px input font prevents iOS Safari zoom on focus."
                leadingIcon={
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <circle cx="11" cy="11" r="8" />
                    <path d="M21 21l-4.35-4.35" />
                  </svg>
                }
              />
              <Input
                label="Owner Password"
                type="password"
                placeholder="Enter at least 8 chars"
                error={pinError}
                onChange={(e) =>
                  setPinError(
                    e.target.value.length >= 8 ? "" : "Must be at least 8 characters"
                  )
                }
              />
            </div>
          </GlassCard>

          <GlassCard>
            <h2 className="demo-section-title">Toasts & Pop-Up Alerts</h2>
            <div className="demo-stack">
              <p className="demo-hero__subtitle">
                Trigger frosted toast notifications positioned safely above mobile BottomNav.
              </p>
              <div className="demo-row">
                <Button
                  variant="primary"
                  size="sm"
                  onClick={() =>
                    pushToast("success", "Person Enrolled", "Saved locally with consent.")
                  }
                >
                  Success Toast
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() =>
                    pushToast("warning", "Stream Reconnecting", "Attempt 2 via TCP...")
                  }
                >
                  Warning Toast
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() =>
                    pushToast("danger", "Zone Intrusion", "Unknown person at Front Gate.")
                  }
                >
                  Danger Toast
                </Button>
              </div>
            </div>
          </GlassCard>
        </div>

        <div className="demo-grid demo-grid--2">
          <GlassCard>
            <h2 className="demo-section-title">Skeleton Loading</h2>
            <div className="demo-stack">
              <div className="demo-skeleton-row">
                <Skeleton variant="circle" width={48} height={48} />
                <div className="demo-skeleton-lines">
                  <Skeleton width="62%" height={16} />
                  <Skeleton width="40%" height={12} />
                </div>
              </div>
              <Skeleton variant="card" height={96} />
            </div>
          </GlassCard>

          <GlassCard>
            <h2 className="demo-section-title">EmptyState</h2>
            <EmptyState
              title="No Restricted Zones Yet"
              description="Draw a polygon zone on your camera snapshot to receive instant intrusion alerts."
              action={
                <Button
                  variant="primary"
                  onClick={() =>
                    pushToast("info", "Zone Editor", "Run tools/draw_zones.py or open Zones.")
                  }
                >
                  Create First Zone
                </Button>
              }
            />
          </GlassCard>
        </div>

        <GlassCard>
          <h2 className="demo-section-title">
            Mobile BottomNav Preview (Also Pinned Live on &lt; 768px)
          </h2>
          <BottomNav
            items={NAV_ITEMS}
            activeId={activeNav}
            onSelect={setActiveNav}
            embedded
          />
        </GlassCard>
      </main>

      <BottomNav items={NAV_ITEMS} activeId={activeNav} onSelect={setActiveNav} />

      <Modal
        open={modalOpen}
        title="Confirm Zone Rule"
        onClose={() => setModalOpen(false)}
        footer={
          <>
            <Button variant="secondary" onClick={() => setModalOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={() => {
                setModalOpen(false);
                pushToast("success", "Saved", "Modal action confirmed.");
              }}
            >
              Confirm
            </Button>
          </>
        }
      >
        <p style={{ margin: 0 }}>
          Frosted modal dialog with keyboard Escape support, backdrop dismissal, safe-area
          padding, and 44px touch controls.
        </p>
      </Modal>

      <ToastStack toasts={toasts} onDismiss={dismissToast} />
    </div>
  );
};

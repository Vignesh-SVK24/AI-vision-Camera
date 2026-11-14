import React, { useCallback, useEffect, useState } from "react";
import type { EventItem } from "./api/types";
import { AuthProvider, useAuth } from "./context/AuthContext";
import {
  BottomNav,
  Button,
  Chip,
  GlassCard,
  NavBar,
  Skeleton,
  ToastStack,
  type NavItem,
  type ToastData,
} from "./components/ui";
import { DesignDemoPage } from "./pages/DesignDemoPage";
import { EventsPage } from "./pages/EventsPage";
import { LivePage } from "./pages/LivePage";
import { LoginPage } from "./pages/LoginPage";
import { NotificationsPage } from "./pages/NotificationsPage";
import { OverviewPage } from "./pages/OverviewPage";
import { PeoplePage } from "./pages/PeoplePage";
import { SettingsPage } from "./pages/SettingsPage";
import { ZonesPage } from "./pages/ZonesPage";

type AppRoute =
  | "/overview"
  | "/live"
  | "/events"
  | "/people"
  | "/zones"
  | "/notifications"
  | "/settings"
  | "/design";

const VALID_ROUTES = new Set<AppRoute>([
  "/overview",
  "/live",
  "/events",
  "/people",
  "/zones",
  "/notifications",
  "/settings",
  "/design",
]);

function normalizeRoute(pathname: string): AppRoute {
  let path = pathname;
  if (window.location.hash && window.location.hash.length > 1) {
    path = window.location.hash.replace(/^#/, "");
  }
  // Strip repository subpath if hosted on GitHub Pages (/AI-vision-Camera/...)
  path = path.replace(/^\/AI-vision-Camera/i, "");
  if (!path || path === "/" || path === "") return "/overview";
  const withSlash = path.startsWith("/") ? path : `/${path}`;
  const trimmed = withSlash.length > 1 ? withSlash.replace(/\/+$/, "") : withSlash;
  return VALID_ROUTES.has(trimmed as AppRoute)
    ? (trimmed as AppRoute)
    : "/overview";
}

const DESKTOP_NAV_ITEMS: NavItem[] = [
  { id: "/overview", label: "Overview", href: "/overview" },
  { id: "/live", label: "Live", href: "/live" },
  { id: "/events", label: "Events", href: "/events" },
  { id: "/people", label: "People", href: "/people" },
  { id: "/zones", label: "Zones", href: "/zones" },
  { id: "/notifications", label: "Alerts", href: "/notifications" },
  { id: "/settings", label: "Settings", href: "/settings" },
];

const MOBILE_BOTTOM_ITEMS: NavItem[] = [
  {
    id: "/overview",
    label: "Home",
    href: "/overview",
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
        <path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z" />
      </svg>
    ),
  },
  {
    id: "/live",
    label: "Live",
    href: "/live",
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
        <path d="M23 7l-7 5 7 5V7z" />
        <rect x="1" y="5" width="15" height="14" rx="2" />
      </svg>
    ),
  },
  {
    id: "/events",
    label: "Events",
    href: "/events",
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
        <path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01" />
      </svg>
    ),
  },
  {
    id: "/people",
    label: "People",
    href: "/people",
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
        <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
        <circle cx="9" cy="7" r="4" />
        <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
        <path d="M16 3.13a4 4 0 0 1 0 7.75" />
      </svg>
    ),
  },
  {
    id: "/zones",
    label: "Zones",
    href: "/zones",
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
        <polygon points="12 2 22 8.5 18 21 6 21 2 8.5 12 2" />
      </svg>
    ),
  },
];

function toastToneForEvent(ev: EventItem): ToastData["tone"] {
  if (
    ev.event_type === "zone_intrusion" ||
    ev.event_type === "camera_offline" ||
    ev.event_type === "system_error"
  ) {
    return "danger";
  }
  if (ev.event_type === "person_detected" && !ev.person_name) {
    return "warning";
  }
  if (ev.event_type === "camera_online") {
    return "success";
  }
  return "info";
}

const DashboardShell: React.FC = () => {
  const { user, isLoading, wsConnected, logout, subscribeEvents } = useAuth();
  const [route, setRoute] = useState<AppRoute>(() =>
    normalizeRoute(window.location.pathname)
  );
  const [toasts, setToasts] = useState<ToastData[]>([]);

  const pushToast = useCallback((toast: Omit<ToastData, "id">) => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    setToasts((prev) => [{ id, ...toast }, ...prev].slice(0, 4));
  }, []);

  const dismissToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((item) => item.id !== id));
  }, []);

  const navigate = useCallback((target: string) => {
    const next = normalizeRoute(target);
    window.location.hash = next;
    setRoute(next);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, []);

  useEffect(() => {
    const onLocationChange = () => {
      setRoute(normalizeRoute(window.location.hash || window.location.pathname));
    };
    window.addEventListener("popstate", onLocationChange);
    window.addEventListener("hashchange", onLocationChange);
    return () => {
      window.removeEventListener("popstate", onLocationChange);
      window.removeEventListener("hashchange", onLocationChange);
    };
  }, []);

  useEffect(() => {
    if (!user) return;
    return subscribeEvents((ev) => {
      const who = ev.person_name ? ev.person_name : "Unknown";
      const where = ev.zone_name ? ` · ${ev.zone_name}` : "";
      pushToast({
        title: ev.event_type,
        description: ev.message || `${who}${where}`,
        tone: toastToneForEvent(ev),
      });
    });
  }, [pushToast, subscribeEvents, user]);

  if (route === "/design") {
    return <DesignDemoPage />;
  }

  if (isLoading) {
    return (
      <div className="login-wrapper">
        <GlassCard className="login-card">
          <div className="stack-md">
            <Skeleton variant="text" width="55%" height={24} />
            <Skeleton variant="card" height={110} />
            <Skeleton variant="text" width="80%" height={16} />
          </div>
        </GlassCard>
      </div>
    );
  }

  if (!user) {
    return <LoginPage />;
  }

  return (
    <div className="app-shell">
      <NavBar
        title="VisionGuard"
        items={DESKTOP_NAV_ITEMS}
        activeId={route}
        onSelect={navigate}
        rightSlot={
          <div className="row-wrap">
            <Chip
              label={wsConnected ? "Live WS" : "WS Reconnecting"}
              variant={wsConnected ? "success" : "warning"}
              dot
            />
            <Button variant="secondary" size="sm" onClick={logout}>
              Sign Out
            </Button>
          </div>
        }
      />

      <div className="page-container" style={{ paddingBottom: 0 }}>
        <div className="mobile-subnav-bar" aria-label="Additional sections">
          <Chip
            label="Alerts"
            selected={route === "/notifications"}
            onClick={() => navigate("/notifications")}
          />
          <Chip
            label="Settings"
            selected={route === "/settings"}
            onClick={() => navigate("/settings")}
          />
          <Chip
            label={wsConnected ? "Live WS" : "Offline WS"}
            variant={wsConnected ? "success" : "warning"}
            dot
          />
          <Chip label="Sign Out" variant="danger" onClick={logout} />
        </div>
      </div>

      <main>
        {route === "/overview" ? <OverviewPage onNavigate={navigate} /> : null}
        {route === "/live" ? <LivePage onNavigate={navigate} /> : null}
        {route === "/events" ? <EventsPage /> : null}
        {route === "/people" ? <PeoplePage onNotify={pushToast} /> : null}
        {route === "/zones" ? <ZonesPage onNotify={pushToast} /> : null}
        {route === "/notifications" ? <NotificationsPage /> : null}
        {route === "/settings" ? <SettingsPage onNotify={pushToast} /> : null}
      </main>

      <BottomNav
        items={MOBILE_BOTTOM_ITEMS}
        activeId={route}
        onSelect={navigate}
      />

      <ToastStack toasts={toasts} onDismiss={dismissToast} />
    </div>
  );
};

export const App: React.FC = () => {
  return (
    <AuthProvider>
      <DashboardShell />
    </AuthProvider>
  );
};

export default App;

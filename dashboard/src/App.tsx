import React, { useCallback, useEffect, useState } from "react";
import type { EventItem } from "./api/types";
import { AuthProvider, useAuth } from "./context/AuthContext";
import {
  Dock,
  GlassCard,
  SideRail,
  Skeleton,
  ToastStack,
  type NavItem,
  type ToastData,
} from "./components/ui";
import { EventsPage } from "./pages/EventsPage";
import { HomePage } from "./pages/HomePage";
import { LoginPage } from "./pages/LoginPage";
import { PeoplePage } from "./pages/PeoplePage";
import { SettingsPage } from "./pages/SettingsPage";

export type AppRoute = "/" | "/events" | "/people" | "/settings";

const VALID_ROUTES = new Set<AppRoute>(["/", "/events", "/people", "/settings"]);

function normalizeRoute(pathname: string): AppRoute {
  let path = pathname;
  if (window.location.hash && window.location.hash.length > 1) {
    path = window.location.hash.replace(/^#/, "");
  }
  // Strip subpath if hosted under repo root
  path = path.replace(/^\/AI-vision-Camera/i, "");
  if (!path || path === "" || path === "/overview" || path === "/live") return "/";
  if (path === "/zones" || path === "/notifications") return path === "/zones" ? "/settings" : "/events";
  const withSlash = path.startsWith("/") ? path : `/${path}`;
  const trimmed = withSlash.length > 1 ? withSlash.replace(/\/+$/, "") : withSlash;
  return VALID_ROUTES.has(trimmed as AppRoute) ? (trimmed as AppRoute) : "/";
}

const NAV_ITEMS: NavItem[] = [
  {
    id: "/",
    label: "Home",
    href: "/",
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z" />
      </svg>
    ),
  },
  {
    id: "/events",
    label: "Events",
    href: "/events",
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01" />
      </svg>
    ),
  },
  {
    id: "/people",
    label: "People",
    href: "/people",
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
        <circle cx="9" cy="7" r="4" />
        <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
        <path d="M16 3.13a4 4 0 0 1 0 7.75" />
      </svg>
    ),
  },
  {
    id: "/settings",
    label: "Settings",
    href: "/settings",
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" />
        <circle cx="12" cy="12" r="3" />
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
  const { user, isLoading, logout, subscribeEvents } = useAuth();
  const [route, setRoute] = useState<AppRoute>(() =>
    normalizeRoute(window.location.hash || window.location.pathname)
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
        title: ev.event_type.replace(/_/g, " "),
        description: ev.message || `${who}${where}`,
        tone: toastToneForEvent(ev),
      });
    });
  }, [pushToast, subscribeEvents, user]);

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
    <div className="lg-app-layout">
      {/* Desktop Navigation: Slim Glass Side Rail */}
      <SideRail
        items={NAV_ITEMS}
        activeId={route}
        onSelect={navigate}
        onLogout={logout}
        brandTitle="VisionGuard"
      />

      {/* Main Content Area */}
      <main className="lg-main-viewport">
        {route === "/" ? <HomePage /> : null}
        {route === "/events" ? <EventsPage /> : null}
        {route === "/people" ? <PeoplePage onNotify={pushToast} /> : null}
        {route === "/settings" ? <SettingsPage onNotify={pushToast} /> : null}
      </main>

      {/* Mobile Navigation: Floating Glass Dock */}
      <Dock
        items={NAV_ITEMS}
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

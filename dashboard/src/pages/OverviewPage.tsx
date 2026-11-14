import React, { useCallback, useEffect, useState } from "react";
import { ApiError, apiClient } from "../api/client";
import type { CameraStatus, EventItem } from "../api/types";
import { useAuth } from "../context/AuthContext";
import { SnapshotImage } from "../components/SnapshotImage";
import { Button, Chip, EmptyState, GlassCard, Skeleton } from "../components/ui";

function todayIsoDate(): string {
  const now = new Date();
  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

function formatTime(iso: string | null): string {
  if (!iso) return "N/A";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export interface OverviewPageProps {
  onNavigate: (route: string) => void;
}

export const OverviewPage: React.FC<OverviewPageProps> = ({ onNavigate }) => {
  const { wsConnected, subscribeEvents } = useAuth();
  const [camera, setCamera] = useState<CameraStatus | null>(null);
  const [todayEvents, setTodayEvents] = useState<EventItem[]>([]);
  const [latestEvents, setLatestEvents] = useState<EventItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    const today = todayIsoDate();
    try {
      const [camResp, todayResp, recentResp] = await Promise.all([
        apiClient.getCameraStatus(),
        apiClient.getEvents({ startDate: today, endDate: today, limit: 500 }),
        apiClient.getEvents({ limit: 8 }),
      ]);
      setCamera(camResp);
      setTodayEvents(todayResp.events);
      setLatestEvents(recentResp.events);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Failed to load dashboard overview."
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  useEffect(() => {
    return subscribeEvents((newEvent) => {
      setLatestEvents((prev) => [newEvent, ...prev.slice(0, 7)]);
      if (newEvent.created_at.startsWith(todayIsoDate())) {
        setTodayEvents((prev) => [newEvent, ...prev]);
      }
      if (
        newEvent.event_type === "camera_online" ||
        newEvent.event_type === "camera_offline"
      ) {
        setCamera((prev) =>
          prev
            ? {
                ...prev,
                status: newEvent.event_type === "camera_online" ? "online" : "offline",
                last_status_change: newEvent.created_at,
                last_event_at: newEvent.created_at,
                last_event_type: newEvent.event_type,
                total_events: prev.total_events + 1,
              }
            : prev
        );
      }
    });
  }, [subscribeEvents]);

  const personToday = todayEvents.filter((e) => e.event_type === "person_detected").length;
  const zonesToday = todayEvents.filter((e) => e.event_type === "zone_intrusion").length;
  const alertsToday = todayEvents.filter(
    (e) => e.event_type === "camera_offline" || e.event_type === "system_error"
  ).length;

  const camVariant =
    camera?.status === "online"
      ? "success"
      : camera?.status === "offline"
      ? "danger"
      : "warning";

  return (
    <div className="page-container">
      <div className="page-header">
        <div>
          <h1 className="page-title">Overview</h1>
          <p className="page-subtitle">
            Camera health, today&apos;s activity, and real-time events.
          </p>
        </div>
        <div className="row-wrap">
          <Chip
            label={wsConnected ? "Live WS Connected" : "WS Reconnecting"}
            variant={wsConnected ? "success" : "warning"}
            dot
          />
          <Button variant="secondary" size="sm" onClick={() => void loadData()}>
            Refresh
          </Button>
        </div>
      </div>

      {error ? (
        <GlassCard>
          <div className="stack-md">
            <div className="error-banner" role="alert">
              {error}
            </div>
            <div>
              <Button variant="primary" onClick={() => void loadData()}>
                Retry
              </Button>
            </div>
          </div>
        </GlassCard>
      ) : null}

      {loading ? (
        <div className="grid-cards grid-cards--4">
          <Skeleton variant="card" height={116} />
          <Skeleton variant="card" height={116} />
          <Skeleton variant="card" height={116} />
          <Skeleton variant="card" height={116} />
        </div>
      ) : !error ? (
        <div className="grid-cards grid-cards--4">
          <GlassCard>
            <div className="stack-sm">
              <span className="stat-label">{camera?.camera_name ?? "Camera"}</span>
              <div>
                <Chip
                  label={(camera?.status ?? "unknown").toUpperCase()}
                  variant={camVariant}
                  dot
                />
              </div>
              <span className="page-subtitle">
                Updated: {formatTime(camera?.last_status_change ?? null)}
              </span>
            </div>
          </GlassCard>

          <GlassCard>
            <div className="stack-sm">
              <span className="stat-label">Events Today</span>
              <span className="stat-value">{todayEvents.length}</span>
              <span className="page-subtitle">
                Total stored: {camera?.total_events ?? 0}
              </span>
            </div>
          </GlassCard>

          <GlassCard>
            <div className="stack-sm">
              <span className="stat-label">People Seen Today</span>
              <span className="stat-value">{personToday}</span>
              <span className="page-subtitle">Cooldown-filtered detections</span>
            </div>
          </GlassCard>

          <GlassCard>
            <div className="stack-sm">
              <span className="stat-label">Zone / System Alerts</span>
              <span className="stat-value">{zonesToday + alertsToday}</span>
              <span className="page-subtitle">
                {zonesToday} zone · {alertsToday} system/offline
              </span>
            </div>
          </GlassCard>
        </div>
      ) : null}

      <GlassCard>
        <div className="page-header" style={{ marginBottom: 14 }}>
          <h2 className="demo-section-title" style={{ margin: 0 }}>
            Latest Events
          </h2>
          <Button variant="secondary" size="sm" onClick={() => onNavigate("/events")}>
            View All Events
          </Button>
        </div>

        {loading ? (
          <div className="stack-md">
            <Skeleton variant="card" height={90} />
            <Skeleton variant="card" height={90} />
          </div>
        ) : latestEvents.length === 0 ? (
          <EmptyState
            title="No Events Recorded Yet"
            description="Once the vision engine detects a person, zone entry, or camera status change, events appear here live."
            action={
              <Button variant="primary" onClick={() => onNavigate("/zones")}>
                Configure Zones
              </Button>
            }
          />
        ) : (
          <div className="stack-md">
            {latestEvents.map((ev) => (
              <GlassCard key={ev.id} compact hoverGlow={false}>
                <div className="event-card">
                  <div className="stack-sm">
                    <div className="row-wrap">
                      <Chip
                        label={ev.event_type}
                        variant={
                          ev.event_type === "zone_intrusion" ||
                          ev.event_type === "camera_offline"
                            ? "danger"
                            : ev.event_type === "person_detected"
                            ? "accent"
                            : "neutral"
                        }
                        dot
                      />
                      {ev.person_name ? (
                        <Chip label={ev.person_name} variant="success" />
                      ) : ev.event_type === "person_detected" ||
                        ev.event_type === "zone_intrusion" ? (
                        <Chip label="Unknown" variant="warning" />
                      ) : null}
                      {ev.zone_name ? <Chip label={ev.zone_name} /> : null}
                    </div>
                    <strong style={{ fontSize: "0.95rem" }}>
                      {ev.message || ev.event_type}
                    </strong>
                    <span className="page-subtitle">{formatTime(ev.created_at)}</span>
                  </div>

                  {ev.snapshot_url ? (
                    <SnapshotImage
                      eventId={ev.id}
                      alt={`Snapshot for event ${ev.id}`}
                      className="event-thumb"
                      onClick={() => onNavigate("/events")}
                    />
                  ) : null}
                </div>
              </GlassCard>
            ))}
          </div>
        )}
      </GlassCard>
    </div>
  );
};

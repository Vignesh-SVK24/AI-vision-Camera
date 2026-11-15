import React, { useEffect, useRef, useState } from "react";
import { apiClient, buildWsLiveUrl, getAccessToken } from "../api/client";
import type { CameraStatus, EventItem } from "../api/types";
import { Chip, GlassCard, Skeleton } from "../components/ui";

const EventSnapshotThumb: React.FC<{ eventId: number; hasSnapshot: boolean }> = ({
  eventId,
  hasSnapshot,
}) => {
  const [blobUrl, setBlobUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!hasSnapshot) return;
    let active = true;
    let url: string | null = null;

    apiClient
      .fetchSnapshotBlobUrl(eventId)
      .then((resUrl) => {
        if (active) {
          url = resUrl;
          setBlobUrl(resUrl);
        } else {
          URL.revokeObjectURL(resUrl);
        }
      })
      .catch(() => {});

    return () => {
      active = false;
      if (url) URL.revokeObjectURL(url);
    };
  }, [eventId, hasSnapshot]);

  if (!hasSnapshot || !blobUrl) {
    return (
      <div className="lg-timeline-nothumb" aria-hidden="true">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
          <rect width="18" height="18" x="3" y="3" rx="2" />
          <circle cx="9" cy="9" r="2" />
          <path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21" />
        </svg>
      </div>
    );
  }

  return (
    <img
      src={blobUrl}
      alt="Event snapshot"
      className="lg-timeline-img"
      loading="lazy"
    />
  );
};

export const HomePage: React.FC = () => {
  const [cameraStatus, setCameraStatus] = useState<CameraStatus | null>(null);
  const [liveJpeg, setLiveJpeg] = useState<string | null>(null);
  const [events, setEvents] = useState<EventItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [liveConnected, setLiveConnected] = useState(false);

  const wsRef = useRef<WebSocket | null>(null);
  const blobUrlRef = useRef<string | null>(null);
  const liveConnectedRef = useRef<boolean>(false);

  const replaceFrameUrl = (nextUrl: string | null) => {
    const prev = blobUrlRef.current;
    blobUrlRef.current = nextUrl;
    setLiveJpeg(nextUrl);
    if (prev && prev !== nextUrl) {
      URL.revokeObjectURL(prev);
    }
  };

  useEffect(() => {
    let mounted = true;

    async function loadData() {
      try {
        const [cam, evts] = await Promise.all([
          apiClient.getCameraStatus().catch(() => null),
          apiClient.getEvents({ limit: 8 }).catch(() => ({ events: [], count: 0 })),
        ]);
        if (mounted) {
          if (cam) setCameraStatus(cam);
          if (evts?.events) setEvents(evts.events);
          setLoading(false);
        }
      } catch {
        if (mounted) setLoading(false);
      }
    }

    void loadData();
    const interval = setInterval(loadData, 4000);
    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    let fallbackTimer: number | null = null;

    const connectLiveWs = () => {
      const token = getAccessToken();
      if (!token) {
        liveConnectedRef.current = false;
        setLiveConnected(false);
        return;
      }

      try {
        const ws = new WebSocket(buildWsLiveUrl(token));
        ws.binaryType = "blob";
        wsRef.current = ws;

        ws.onopen = () => {
          if (!cancelled) {
            liveConnectedRef.current = true;
            setLiveConnected(true);
          }
        };

        ws.onmessage = (event) => {
          if (cancelled) return;
          if (typeof event.data === "string") return;

          const blob =
            event.data instanceof Blob
              ? event.data
              : new Blob([event.data as ArrayBuffer], { type: "image/jpeg" });
          const nextUrl = URL.createObjectURL(blob);
          replaceFrameUrl(nextUrl);
          liveConnectedRef.current = true;
          setLiveConnected(true);
        };

        ws.onerror = () => {
          if (!cancelled) {
            liveConnectedRef.current = false;
            setLiveConnected(false);
          }
        };

        ws.onclose = () => {
          if (!cancelled) {
            liveConnectedRef.current = false;
            setLiveConnected(false);
          }
        };
      } catch {
        if (!cancelled) {
          liveConnectedRef.current = false;
          setLiveConnected(false);
        }
      }
    };

    connectLiveWs();

    fallbackTimer = window.setInterval(async () => {
      if (!liveConnectedRef.current && !cancelled) {
        try {
          const url = await apiClient.fetchLiveSnapshotBlobUrl();
          if (!cancelled) {
            replaceFrameUrl(url);
          }
        } catch {}
      }
    }, 2500);

    return () => {
      cancelled = true;
      liveConnectedRef.current = false;
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
      if (fallbackTimer !== null) clearInterval(fallbackTimer);
      replaceFrameUrl(null);
    };
  }, []);

  const isOnline = cameraStatus?.status === "online" || liveConnected;
  const recentIntrusion = events.find((e) => e.event_type.includes("intrusion") || e.event_type.includes("zone"));
  const peopleInViewCount = events.filter(
    (e) => e.event_type === "person_detected" && Date.now() - new Date(e.created_at).getTime() < 60000
  ).length;

  return (
    <div className="lg-home-layout">
      {/* 1. Large Hero Live-View */}
      <section className="lg-home-hero">
        <GlassCard variant="elevated" className="lg-live-hero-card">
          <div className="lg-live-hero-header">
            <h1 className="lg-live-title">{cameraStatus?.camera_name || "Live View"}</h1>
            <Chip
              variant={isOnline ? "success" : "danger"}
              label={isOnline ? "Live" : "Offline"}
              dot
            />
          </div>

          <div className="lg-live-viewport">
            {liveJpeg ? (
              <img
                src={liveJpeg}
                alt="Live camera feed"
                className="lg-live-stream-img"
              />
            ) : (
              <div className="lg-live-placeholder">
                <div className="lg-live-spinner" aria-hidden="true" />
                <p className="lg-live-placeholder-text">
                  {isOnline ? "Streaming video..." : "Camera offline"}
                </p>
              </div>
            )}
          </div>
        </GlassCard>
      </section>

      {/* 2. Compact "Now" Quick Stats */}
      <section className="lg-now-strip" aria-label="System status">
        <GlassCard className="lg-now-chip-card" compact>
          <span className="lg-now-label">Presence</span>
          <strong className="lg-now-value">
            {peopleInViewCount > 0 ? `${peopleInViewCount} Active` : "Clear"}
          </strong>
        </GlassCard>

        <GlassCard className="lg-now-chip-card" compact>
          <span className="lg-now-label">Security</span>
          <strong className="lg-now-value">
            {recentIntrusion ? "Intrusion" : "All Clear"}
          </strong>
        </GlassCard>

        <GlassCard className="lg-now-chip-card" compact>
          <span className="lg-now-label">System</span>
          <strong className="lg-now-value">
            <span className={`lg-health-indicator ${isOnline ? "online" : "offline"}`} />
            {isOnline ? "Online" : "Offline"}
          </strong>
        </GlassCard>
      </section>

      {/* 3. Clean Activity Timeline */}
      <section className="lg-home-timeline-section">
        <div className="lg-section-header">
          <h2 className="lg-section-title">Recent Activity</h2>
        </div>

        {loading ? (
          <div className="lg-timeline-skeleton">
            <Skeleton variant="card" height={60} />
            <Skeleton variant="card" height={60} />
            <Skeleton variant="card" height={60} />
          </div>
        ) : events.length === 0 ? (
          <GlassCard className="lg-timeline-empty" compact>
            <p>No recent activity recorded.</p>
          </GlassCard>
        ) : (
          <div className="lg-timeline-feed">
            {events.map((evt) => {
              const timeStr = new Date(evt.created_at).toLocaleTimeString([], {
                hour: "numeric",
                minute: "2-digit",
              });
              const isAlert = evt.event_type.includes("intrusion") || evt.event_type.includes("unknown");
              const label = evt.person_name || evt.zone_name || (evt.event_type === "person_detected" ? "Person Detected" : evt.event_type.replace(/_/g, " "));

              return (
                <GlassCard key={evt.id} className="lg-timeline-row" compact>
                  <div className="lg-timeline-thumb">
                    <EventSnapshotThumb eventId={evt.id} hasSnapshot={Boolean(evt.snapshot_url)} />
                  </div>

                  <div className="lg-timeline-details">
                    <div className="lg-timeline-top">
                      <span className="lg-timeline-person">{label}</span>
                      <time className="lg-timeline-time">{timeStr}</time>
                    </div>

                    <div className="lg-timeline-bottom">
                      <span className="lg-timeline-type">
                        {evt.event_type.replace(/_/g, " ")}
                      </span>
                      {isAlert && <span className="lg-timeline-alert-tag">Alert</span>}
                    </div>
                  </div>
                </GlassCard>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
};

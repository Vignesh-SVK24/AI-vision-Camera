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
      .catch(() => {
        // ignore thumbnail failure
      });

    return () => {
      active = false;
      if (url) URL.revokeObjectURL(url);
    };
  }, [eventId, hasSnapshot]);

  if (!hasSnapshot || !blobUrl) {
    return (
      <div className="lg-timeline-nothumb" aria-hidden="true">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
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
      alt={`Event #${eventId} snapshot`}
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
  const [streamError, setStreamError] = useState<string | null>(null);

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

  // Poll camera status and recent events
  useEffect(() => {
    let mounted = true;

    async function loadData() {
      try {
        const [cam, evts] = await Promise.all([
          apiClient.getCameraStatus().catch(() => null),
          apiClient.getEvents({ limit: 12 }).catch(() => ({ events: [], count: 0 })),
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

  // WebSocket Live Stream Connection
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
            setStreamError(null);
          }
        };

        ws.onmessage = (event) => {
          if (cancelled) return;
          if (typeof event.data === "string") {
            try {
              const meta = JSON.parse(event.data);
              if (meta.error) setStreamError(meta.error);
            } catch {
              // ignore
            }
            return;
          }

          const blob =
            event.data instanceof Blob
              ? event.data
              : new Blob([event.data as ArrayBuffer], { type: "image/jpeg" });
          const nextUrl = URL.createObjectURL(blob);
          replaceFrameUrl(nextUrl);
          liveConnectedRef.current = true;
          setLiveConnected(true);
          setStreamError(null);
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

    // Periodic fallback snapshot if WS is offline
    fallbackTimer = window.setInterval(async () => {
      if (!liveConnectedRef.current && !cancelled) {
        try {
          const url = await apiClient.fetchLiveSnapshotBlobUrl();
          if (!cancelled) {
            replaceFrameUrl(url);
          }
        } catch {
          // ignore
        }
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
  const latestEvent = events[0];
  const peopleInViewCount = events.filter(
    (e) => e.event_type === "person_detected" && Date.now() - new Date(e.created_at).getTime() < 60000
  ).length;

  return (
    <div className="lg-home-layout">
      {/* 1. Large Live-View Glass Card as Hero */}
      <section className="lg-home-hero">
        <GlassCard variant="elevated" className="lg-live-hero-card">
          <div className="lg-live-hero-header">
            <div className="lg-live-header-meta">
              <h1 className="lg-live-title">{cameraStatus?.camera_name || "Live View"}</h1>
              <span className="lg-live-source">Primary Camera Feed</span>
            </div>
            <div className="lg-live-header-chips">
              <Chip
                variant={isOnline ? "success" : "danger"}
                label={isOnline ? "LIVE" : "OFFLINE"}
                dot
              />
            </div>
          </div>

          <div className="lg-live-viewport">
            {liveJpeg ? (
              <img
                src={liveJpeg}
                alt="Live camera stream"
                className="lg-live-stream-img"
              />
            ) : (
              <div className="lg-live-placeholder">
                <div className="lg-live-spinner" aria-hidden="true" />
                <p className="lg-live-placeholder-text">
                  {streamError
                    ? `Stream Notice: ${streamError}`
                    : isOnline
                    ? "Acquiring live video frames..."
                    : "Connecting to camera..."}
                </p>
              </div>
            )}
          </div>
        </GlassCard>
      </section>

      {/* 2. Compact "Now" Strip */}
      <section className="lg-now-strip" aria-label="Current activity summary">
        <GlassCard className="lg-now-chip-card" compact>
          <span className="lg-now-label">People in view</span>
          <span className="lg-now-value">
            {peopleInViewCount > 0 ? `${peopleInViewCount} active` : "None"}
          </span>
        </GlassCard>

        <GlassCard className="lg-now-chip-card" compact>
          <span className="lg-now-label">Last alert</span>
          <span className="lg-now-value" title={latestEvent?.event_type}>
            {recentIntrusion
              ? recentIntrusion.zone_name || "Intrusion detected"
              : latestEvent
              ? latestEvent.person_name || latestEvent.event_type.replace(/_/g, " ")
              : "All clear"}
          </span>
        </GlassCard>

        <GlassCard className="lg-now-chip-card" compact>
          <span className="lg-now-label">System health</span>
          <span className="lg-now-value">
            <span className={`lg-health-indicator ${isOnline ? "online" : "offline"}`} />
            {isOnline ? "Operational" : "Degraded"}
          </span>
        </GlassCard>
      </section>

      {/* 3. Vertical Timeline of Latest Events */}
      <section className="lg-home-timeline-section">
        <div className="lg-section-header">
          <h2 className="lg-section-title">Recent Events</h2>
          <span className="lg-section-subtitle">{events.length} logged items</span>
        </div>

        {loading ? (
          <div className="lg-timeline-skeleton">
            <Skeleton variant="card" height={68} />
            <Skeleton variant="card" height={68} />
            <Skeleton variant="card" height={68} />
          </div>
        ) : events.length === 0 ? (
          <GlassCard className="lg-timeline-empty" compact>
            <p>No activity detected yet. Security events appear here in real time.</p>
          </GlassCard>
        ) : (
          <div className="lg-timeline-feed">
            {events.map((evt) => {
              const timeStr = new Date(evt.created_at).toLocaleTimeString([], {
                hour: "2-digit",
                minute: "2-digit",
                second: "2-digit",
              });
              const isAlert = evt.event_type.includes("intrusion") || evt.event_type.includes("unknown");
              return (
                <GlassCard key={evt.id} className="lg-timeline-row" compact>
                  <div className="lg-timeline-thumb">
                    <EventSnapshotThumb eventId={evt.id} hasSnapshot={Boolean(evt.snapshot_url)} />
                  </div>

                  <div className="lg-timeline-details">
                    <div className="lg-timeline-top">
                      <span className="lg-timeline-type">
                        {evt.event_type.replace(/_/g, " ").toUpperCase()}
                      </span>
                      <time className="lg-timeline-time">{timeStr}</time>
                    </div>

                    <div className="lg-timeline-bottom">
                      {evt.person_name ? (
                        <span className="lg-timeline-person">
                          <strong>Person:</strong> {evt.person_name}
                        </span>
                      ) : evt.zone_name ? (
                        <span className="lg-timeline-zone">
                          <strong>Zone:</strong> {evt.zone_name}
                        </span>
                      ) : (
                        <span className="lg-timeline-person">Unknown presence</span>
                      )}
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

import React, { useCallback, useEffect, useState } from "react";
import { apiClient, ApiError } from "../api/client";
import type { EventItem, NotificationItem } from "../api/types";
import { Button, Chip, EmptyState, GlassCard, Modal, Skeleton } from "../components/ui";
import { useAuth } from "../context/AuthContext";

type ViewTab = "events" | "alerts";
type DateFilter = "all" | "today" | "week";
type TypeFilter = "all" | "person_detected" | "zone_intrusion" | "camera_offline" | "camera_online";

const SnapshotImage: React.FC<{ eventId: number; hasSnapshot: boolean; alt: string; className?: string }> = ({
  eventId,
  hasSnapshot,
  alt,
  className = "",
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
        // ignore
      });

    return () => {
      active = false;
      if (url) URL.revokeObjectURL(url);
    };
  }, [eventId, hasSnapshot]);

  if (!hasSnapshot || !blobUrl) {
    return (
      <div className={`lg-event-placeholder ${className}`}>
        <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
          <rect width="18" height="18" x="3" y="3" rx="2" />
          <circle cx="9" cy="9" r="2" />
          <path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21" />
        </svg>
      </div>
    );
  }

  return <img src={blobUrl} alt={alt} className={className} loading="lazy" />;
};

export const EventsPage: React.FC = () => {
  const { subscribeEvents } = useAuth();
  const [activeTab, setActiveTab] = useState<ViewTab>("events");

  // Events state
  const [events, setEvents] = useState<EventItem[]>([]);
  const [loadingEvents, setLoadingEvents] = useState(true);
  const [eventsError, setEventsError] = useState<string | null>(null);
  const [typeFilter, setTypeFilter] = useState<TypeFilter>("all");
  const [dateFilter, setDateFilter] = useState<DateFilter>("all");
  const [personFilter, setPersonFilter] = useState<"all" | "known" | "unknown">("all");
  const [selectedEvent, setSelectedEvent] = useState<EventItem | null>(null);

  // Alerts state
  const [alerts, setAlerts] = useState<NotificationItem[]>([]);
  const [loadingAlerts, setLoadingAlerts] = useState(false);
  const [alertsError, setAlertsError] = useState<string | null>(null);
  const [alertStatusFilter, setAlertStatusFilter] = useState<"all" | "sent" | "pending">("all");

  // Load events
  const loadEvents = useCallback(async () => {
    setLoadingEvents(true);
    setEventsError(null);
    try {
      let startDate: string | undefined;
      if (dateFilter === "today") {
        const today = new Date();
        today.setHours(0, 0, 0, 0);
        startDate = today.toISOString();
      } else if (dateFilter === "week") {
        const weekAgo = new Date(Date.now() - 7 * 86400000);
        startDate = weekAgo.toISOString();
      }

      const res = await apiClient.getEvents({
        eventType: typeFilter !== "all" ? typeFilter : undefined,
        startDate,
        limit: 100,
      });
      setEvents(res.events || []);
    } catch (err) {
      setEventsError(err instanceof ApiError ? err.message : "Failed to load events.");
    } finally {
      setLoadingEvents(false);
    }
  }, [typeFilter, dateFilter]);

  // Load alerts
  const loadAlerts = useCallback(async () => {
    setLoadingAlerts(true);
    setAlertsError(null);
    try {
      const list = await apiClient.getNotifications(100);
      setAlerts(list || []);
    } catch (err) {
      setAlertsError(err instanceof ApiError ? err.message : "Failed to load alerts history.");
    } finally {
      setLoadingAlerts(false);
    }
  }, []);

  useEffect(() => {
    if (activeTab === "events") {
      void loadEvents();
    } else {
      void loadAlerts();
    }
  }, [activeTab, loadEvents, loadAlerts]);

  // Real-time event updates
  useEffect(() => {
    return subscribeEvents((ev) => {
      setEvents((prev) => [ev, ...prev]);
    });
  }, [subscribeEvents]);

  // Filter events client-side for person category
  const filteredEvents = events.filter((e) => {
    if (personFilter === "known") return Boolean(e.person_name && e.person_name !== "Unknown");
    if (personFilter === "unknown") return e.person_name === "Unknown" || !e.person_name;
    return true;
  });

  // Filter alerts
  const filteredAlerts = alerts.filter((a) => {
    if (alertStatusFilter === "sent") return a.notified;
    if (alertStatusFilter === "pending") return !a.notified;
    return true;
  });

  return (
    <div className="lg-events-page">
      {/* Header and View Switcher */}
      <header className="lg-page-header">
        <div>
          <h1 className="lg-page-title">Activity</h1>
          <p className="lg-page-subtitle">Security events and notification records</p>
        </div>
        <div className="lg-tab-switcher">
          <Chip
            label={`Events (${filteredEvents.length})`}
            selected={activeTab === "events"}
            onClick={() => setActiveTab("events")}
          />
          <Chip
            label={`Alerts (${alerts.length})`}
            selected={activeTab === "alerts"}
            onClick={() => setActiveTab("alerts")}
          />
        </div>
      </header>

      {/* EVENTS VIEW */}
      {activeTab === "events" && (
        <section className="lg-events-content">
          {/* Simple Filter Chips */}
          <GlassCard className="lg-filter-bar" compact>
            <div className="lg-filter-group">
              <span className="lg-filter-title">Type:</span>
              <div className="lg-chip-row">
                <Chip label="All" selected={typeFilter === "all"} onClick={() => setTypeFilter("all")} />
                <Chip
                  label="Person"
                  selected={typeFilter === "person_detected"}
                  onClick={() => setTypeFilter("person_detected")}
                />
                <Chip
                  label="Intrusion"
                  selected={typeFilter === "zone_intrusion"}
                  onClick={() => setTypeFilter("zone_intrusion")}
                />
                <Chip
                  label="Offline"
                  selected={typeFilter === "camera_offline"}
                  onClick={() => setTypeFilter("camera_offline")}
                />
              </div>
            </div>

            <div className="lg-filter-group">
              <span className="lg-filter-title">Person:</span>
              <div className="lg-chip-row">
                <Chip label="All" selected={personFilter === "all"} onClick={() => setPersonFilter("all")} />
                <Chip label="Known" selected={personFilter === "known"} onClick={() => setPersonFilter("known")} />
                <Chip label="Unknown" selected={personFilter === "unknown"} onClick={() => setPersonFilter("unknown")} />
              </div>
            </div>

            <div className="lg-filter-group">
              <span className="lg-filter-title">Date:</span>
              <div className="lg-chip-row">
                <Chip label="All Time" selected={dateFilter === "all"} onClick={() => setDateFilter("all")} />
                <Chip label="Today" selected={dateFilter === "today"} onClick={() => setDateFilter("today")} />
                <Chip label="Past 7 Days" selected={dateFilter === "week"} onClick={() => setDateFilter("week")} />
              </div>
            </div>
          </GlassCard>

          {eventsError && (
            <div className="lg-error-banner" role="alert">
              <span>{eventsError}</span>
              <Button size="sm" variant="secondary" onClick={() => void loadEvents()}>
                Retry
              </Button>
            </div>
          )}

          {loadingEvents ? (
            <div className="lg-event-grid">
              {Array.from({ length: 6 }).map((_, i) => (
                <GlassCard key={i} className="lg-event-card-skeleton">
                  <Skeleton variant="card" height={150} />
                  <div style={{ marginTop: 8 }}>
                    <Skeleton variant="text" width="60%" height={16} />
                  </div>
                </GlassCard>
              ))}
            </div>
          ) : filteredEvents.length === 0 ? (
            <GlassCard>
              <EmptyState
                title="No events found"
                description="No security events match the selected criteria."
              />
            </GlassCard>
          ) : (
            <div className="lg-event-grid">
              {filteredEvents.map((evt) => {
                const isAlert = evt.event_type.includes("intrusion") || evt.event_type.includes("unknown");
                return (
                  <GlassCard
                    key={evt.id}
                    className="lg-event-card"
                    onClick={() => setSelectedEvent(evt)}
                  >
                    <div className="lg-event-card-media">
                      <SnapshotImage
                        eventId={evt.id}
                        hasSnapshot={Boolean(evt.snapshot_url)}
                        alt={`Event #${evt.id}`}
                        className="lg-event-card-img"
                      />
                      <span className={`lg-event-badge ${isAlert ? "alert" : ""}`}>
                        {evt.event_type.replace(/_/g, " ")}
                      </span>
                    </div>

                    <div className="lg-event-card-info">
                      <div className="lg-event-card-header">
                        <span className="lg-event-name">
                          {evt.person_name || evt.zone_name || "Unidentified"}
                        </span>
                        <time className="lg-event-time">
                          {new Date(evt.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                        </time>
                      </div>

                      <div className="lg-event-card-meta">
                        <span>Event #{evt.id}</span>
                        {evt.confidence && (
                          <span>{(evt.confidence * 100).toFixed(0)}% match</span>
                        )}
                      </div>
                    </div>
                  </GlassCard>
                );
              })}
            </div>
          )}
        </section>
      )}

      {/* ALERTS NOTIFICATION HISTORY VIEW */}
      {activeTab === "alerts" && (
        <section className="lg-alerts-content">
          <GlassCard className="lg-filter-bar" compact>
            <div className="lg-filter-group">
              <span className="lg-filter-title">Delivery Status:</span>
              <div className="lg-chip-row">
                <Chip
                  label="All"
                  selected={alertStatusFilter === "all"}
                  onClick={() => setAlertStatusFilter("all")}
                />
                <Chip
                  label="Sent"
                  selected={alertStatusFilter === "sent"}
                  onClick={() => setAlertStatusFilter("sent")}
                />
                <Chip
                  label="Pending / Failed"
                  selected={alertStatusFilter === "pending"}
                  onClick={() => setAlertStatusFilter("pending")}
                />
              </div>
            </div>
          </GlassCard>

          {alertsError && (
            <div className="lg-error-banner" role="alert">
              <span>{alertsError}</span>
              <Button size="sm" variant="secondary" onClick={() => void loadAlerts()}>
                Retry
              </Button>
            </div>
          )}

          {loadingAlerts ? (
            <div className="lg-alerts-list">
              <Skeleton variant="card" height={72} />
              <Skeleton variant="card" height={72} />
              <Skeleton variant="card" height={72} />
            </div>
          ) : filteredAlerts.length === 0 ? (
            <GlassCard>
              <EmptyState
                title="No alert records"
                description="Telegram notifications sent by the system will be tracked here."
              />
            </GlassCard>
          ) : (
            <div className="lg-alerts-list">
              {filteredAlerts.map((alert) => (
                <GlassCard key={alert.id} className="lg-alert-row" compact>
                  <div className="lg-alert-status-icon">
                    <span
                      className={`lg-alert-status-dot ${
                        alert.notified ? "sent" : "failed"
                      }`}
                    />
                  </div>

                  <div className="lg-alert-info">
                    <div className="lg-alert-title-row">
                      <strong className="lg-alert-type">
                        {alert.event_type.replace(/_/g, " ").toUpperCase()}
                      </strong>
                      <time className="lg-alert-time">
                        {new Date(alert.event_created_at).toLocaleString()}
                      </time>
                    </div>

                    <p className="lg-alert-message">
                      {alert.message || `Notification for event #${alert.event_id}`}
                    </p>

                    <div className="lg-alert-meta-row">
                      <span className="lg-alert-meta">
                        Attempts: {alert.notify_attempts}
                      </span>
                      {alert.last_error && (
                        <span className="lg-alert-error" title={alert.last_error}>
                          Error: {alert.last_error}
                        </span>
                      )}
                    </div>
                  </div>
                </GlassCard>
              ))}
            </div>
          )}
        </section>
      )}

      {/* Lightbox Modal for Selected Event */}
      <Modal
        open={Boolean(selectedEvent)}
        onClose={() => setSelectedEvent(null)}
        title={selectedEvent ? `Event #${selectedEvent.id}: ${selectedEvent.event_type.replace(/_/g, " ")}` : "Event"}
      >
        {selectedEvent && (
          <div className="lg-lightbox-body">
            <div className="lg-lightbox-media">
              <SnapshotImage
                eventId={selectedEvent.id}
                hasSnapshot={Boolean(selectedEvent.snapshot_url)}
                alt={`Event #${selectedEvent.id} full snapshot`}
                className="lg-lightbox-img"
              />
            </div>

            <div className="lg-lightbox-details">
              <div className="lg-lightbox-row">
                <span className="lg-lightbox-label">Recorded:</span>
                <span className="lg-lightbox-val">{new Date(selectedEvent.created_at).toLocaleString()}</span>
              </div>
              <div className="lg-lightbox-row">
                <span className="lg-lightbox-label">Identity:</span>
                <span className="lg-lightbox-val">{selectedEvent.person_name || "Unknown"}</span>
              </div>
              {selectedEvent.zone_name && (
                <div className="lg-lightbox-row">
                  <span className="lg-lightbox-label">Zone:</span>
                  <span className="lg-lightbox-val">{selectedEvent.zone_name}</span>
                </div>
              )}
              {selectedEvent.confidence && (
                <div className="lg-lightbox-row">
                  <span className="lg-lightbox-label">Confidence:</span>
                  <span className="lg-lightbox-val">{(selectedEvent.confidence * 100).toFixed(1)}%</span>
                </div>
              )}
              {selectedEvent.message && (
                <div className="lg-lightbox-row">
                  <span className="lg-lightbox-label">Notice:</span>
                  <span className="lg-lightbox-val">{selectedEvent.message}</span>
                </div>
              )}
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
};

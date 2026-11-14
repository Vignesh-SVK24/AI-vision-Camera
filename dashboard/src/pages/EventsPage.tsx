import React, { useCallback, useEffect, useState } from "react";
import { ApiError, apiClient } from "../api/client";
import type { EventItem } from "../api/types";
import { useAuth } from "../context/AuthContext";
import { SnapshotImage } from "../components/SnapshotImage";
import {
  Button,
  Chip,
  EmptyState,
  GlassCard,
  Input,
  Modal,
  Skeleton,
} from "../components/ui";

const EVENT_TYPES = [
  "all",
  "person_detected",
  "zone_intrusion",
  "camera_offline",
  "camera_online",
  "system_error",
];

export const EventsPage: React.FC = () => {
  const { subscribeEvents } = useAuth();
  const [events, setEvents] = useState<EventItem[]>([]);
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [eventType, setEventType] = useState("all");
  const [personName, setPersonName] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lightboxEvent, setLightboxEvent] = useState<EventItem | null>(null);

  const loadEvents = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiClient.getEvents({
        startDate: startDate || undefined,
        endDate: endDate || undefined,
        eventType,
        personName: personName.trim() || undefined,
        limit: 100,
      });
      setEvents(res.events);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load events.");
    } finally {
      setLoading(false);
    }
  }, [startDate, endDate, eventType, personName]);

  useEffect(() => {
    void loadEvents();
  }, [loadEvents]);

  useEffect(() => {
    return subscribeEvents((ev) => {
      if (eventType !== "all" && ev.event_type !== eventType) return;
      setEvents((prev) => [ev, ...prev]);
    });
  }, [subscribeEvents, eventType]);

  return (
    <div className="page-container">
      <div className="page-header">
        <div>
          <h1 className="page-title">Events</h1>
          <p className="page-subtitle">
            Filter recorded detections and tap any snapshot to open the lightbox.
          </p>
        </div>
        <Button variant="secondary" size="sm" onClick={() => void loadEvents()}>
          Refresh
        </Button>
      </div>

      <GlassCard>
        <div className="stack-md">
          <div className="filter-grid">
            <Input
              label="From Date"
              type="date"
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
            />
            <Input
              label="To Date"
              type="date"
              value={endDate}
              onChange={(e) => setEndDate(e.target.value)}
            />
            <Input
              label="Person Name"
              placeholder="e.g. Alice"
              value={personName}
              onChange={(e) => setPersonName(e.target.value)}
            />
          </div>

          <div className="row-wrap">
            {EVENT_TYPES.map((typeOption) => (
              <Chip
                key={typeOption}
                label={typeOption === "all" ? "All Types" : typeOption}
                selected={eventType === typeOption}
                onClick={() => setEventType(typeOption)}
              />
            ))}
            {(startDate || endDate || personName || eventType !== "all") ? (
              <Button
                variant="secondary"
                size="sm"
                onClick={() => {
                  setStartDate("");
                  setEndDate("");
                  setPersonName("");
                  setEventType("all");
                }}
              >
                Clear Filters
              </Button>
            ) : null}
          </div>
        </div>
      </GlassCard>

      {error ? (
        <GlassCard>
          <div className="stack-md">
            <div className="error-banner" role="alert">
              {error}
            </div>
            <div>
              <Button variant="primary" onClick={() => void loadEvents()}>
                Retry
              </Button>
            </div>
          </div>
        </GlassCard>
      ) : null}

      {loading ? (
        <div className="stack-md">
          <Skeleton variant="card" height={112} />
          <Skeleton variant="card" height={112} />
          <Skeleton variant="card" height={112} />
        </div>
      ) : !error && events.length === 0 ? (
        <GlassCard>
          <EmptyState
            title="No Matching Events"
            description="No events matched your selected date or type filters."
            action={
              <Button
                variant="secondary"
                onClick={() => {
                  setStartDate("");
                  setEndDate("");
                  setPersonName("");
                  setEventType("all");
                }}
              >
                Reset Filters
              </Button>
            }
          />
        </GlassCard>
      ) : (
        <div className="stack-md">
          {events.map((ev) => (
            <GlassCard key={ev.id} compact>
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
                    ) : null}
                    {ev.zone_name ? <Chip label={ev.zone_name} /> : null}
                    {typeof ev.confidence === "number" ? (
                      <Chip label={`Conf ${(ev.confidence * 100).toFixed(0)}%`} />
                    ) : null}
                  </div>
                  <strong>{ev.message || ev.event_type}</strong>
                  <span className="page-subtitle">
                    {new Date(ev.created_at).toLocaleString()} · Event #{ev.id}
                  </span>
                </div>

                {ev.snapshot_url ? (
                  <SnapshotImage
                    eventId={ev.id}
                    alt={`Event ${ev.id} snapshot`}
                    className="event-thumb"
                    onClick={() => setLightboxEvent(ev)}
                  />
                ) : null}
              </div>
            </GlassCard>
          ))}
        </div>
      )}

      <Modal
        open={lightboxEvent !== null}
        title={
          lightboxEvent
            ? `Event #${lightboxEvent.id} · ${lightboxEvent.event_type}`
            : "Snapshot"
        }
        onClose={() => setLightboxEvent(null)}
        footer={
          <Button variant="primary" onClick={() => setLightboxEvent(null)}>
            Close
          </Button>
        }
      >
        {lightboxEvent ? (
          <div className="stack-md">
            <SnapshotImage
              eventId={lightboxEvent.id}
              alt={`Full snapshot for event ${lightboxEvent.id}`}
              className="lightbox-img"
            />
            <div className="stack-sm">
              <strong>{lightboxEvent.message || lightboxEvent.event_type}</strong>
              <span className="page-subtitle">
                Time: {new Date(lightboxEvent.created_at).toLocaleString()}
              </span>
              {lightboxEvent.zone_name ? (
                <span className="page-subtitle">Zone: {lightboxEvent.zone_name}</span>
              ) : null}
            </div>
          </div>
        ) : null}
      </Modal>
    </div>
  );
};

import React, { useCallback, useEffect, useState } from "react";
import { apiClient, ApiError } from "../api/client";
import type { NotificationItem } from "../api/types";
import { Button, Chip, EmptyState, GlassCard, Skeleton } from "../components/ui";

type DeliveryFilter = "all" | "sent" | "pending";

export const NotificationsPage: React.FC = () => {
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [filter, setFilter] = useState<DeliveryFilter>("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadNotifications = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const list = await apiClient.getNotifications(100);
      setItems(list);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Failed to load notifications history."
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadNotifications();
  }, [loadNotifications]);

  const filtered = items.filter((item) => {
    if (filter === "sent") return item.notified;
    if (filter === "pending") return !item.notified;
    return true;
  });

  return (
    <div className="page-container">
      <header className="page-header">
        <div>
          <h1 className="page-title">Notifications</h1>
          <p className="page-subtitle">
            Reliable outbox delivery log for Telegram and downstream alerts.
          </p>
        </div>
        <Button variant="secondary" onClick={() => void loadNotifications()} disabled={loading}>
          Refresh
        </Button>
      </header>

      <div className="row-wrap">
        <Chip
          label={`All (${items.length})`}
          selected={filter === "all"}
          onClick={() => setFilter("all")}
        />
        <Chip
          label={`Sent (${items.filter((i) => i.notified).length})`}
          variant="success"
          selected={filter === "sent"}
          onClick={() => setFilter("sent")}
        />
        <Chip
          label={`Pending / Failed (${items.filter((i) => !i.notified).length})`}
          variant="warning"
          selected={filter === "pending"}
          onClick={() => setFilter("pending")}
        />
      </div>

      {error ? (
        <div className="error-banner row-wrap" role="alert">
          <span>{error}</span>
          <Button size="sm" variant="secondary" onClick={() => void loadNotifications()}>
            Retry
          </Button>
        </div>
      ) : null}

      {loading ? (
        <div className="stack-md">
          <GlassCard>
            <Skeleton variant="card" height={88} />
          </GlassCard>
          <GlassCard>
            <Skeleton variant="card" height={88} />
          </GlassCard>
        </div>
      ) : filtered.length === 0 ? (
        <GlassCard>
          <EmptyState
            title="No notifications in this view"
            description="Outbox entries appear automatically when alerts or status transitions occur."
          />
        </GlassCard>
      ) : (
        <div className="stack-md">
          {filtered.map((item) => (
            <GlassCard key={item.id}>
              <div className="stack-sm">
                <div className="page-header">
                  <div className="row-wrap">
                    <strong>{item.event_type}</strong>
                    {item.zone_name ? (
                      <Chip label={item.zone_name} variant="accent" />
                    ) : null}
                  </div>
                  <div className="row-wrap">
                    <Chip
                      label={
                        item.notified
                          ? "Delivered"
                          : item.last_error
                          ? "Retry queued"
                          : "Pending"
                      }
                      variant={
                        item.notified
                          ? "success"
                          : item.last_error
                          ? "danger"
                          : "warning"
                      }
                      dot
                    />
                    <Chip label={`Attempts: ${item.notify_attempts}`} variant="neutral" />
                  </div>
                </div>

                <p className="page-subtitle">
                  Event #{item.event_id} ·{" "}
                  {new Date(item.event_created_at).toLocaleString()}
                </p>

                {item.message ? <p style={{ margin: 0 }}>{item.message}</p> : null}

                {item.last_error ? (
                  <div className="error-banner">{item.last_error}</div>
                ) : null}
              </div>
            </GlassCard>
          ))}
        </div>
      )}
    </div>
  );
};

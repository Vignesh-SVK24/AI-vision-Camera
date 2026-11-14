import React, { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, apiClient, buildWsLiveUrl, getAccessToken } from "../api/client";
import type { CameraStatus } from "../api/types";
import { Button, Chip, EmptyState, GlassCard, Skeleton } from "../components/ui";

export interface LivePageProps {
  onNavigate: (route: string) => void;
}

type StreamState = "connecting" | "connected" | "paused" | "offline";

interface LiveStatusPayload {
  type?: string;
  status?: string;
  camera_name?: string;
  fps?: number;
  error?: string | null;
}

export const LivePage: React.FC<LivePageProps> = ({ onNavigate }) => {
  const [status, setStatus] = useState<CameraStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [paused, setPaused] = useState(false);
  const [streamState, setStreamState] = useState<StreamState>("connecting");
  const [streamError, setStreamError] = useState<string | null>(null);
  const [liveFps, setLiveFps] = useState<number | null>(null);
  const [liveCameraName, setLiveCameraName] = useState<string | null>(null);
  const [frameUrl, setFrameUrl] = useState<string | null>(null);
  const [lastUpdatedAt, setLastUpdatedAt] = useState<string | null>(null);
  const [snapshotBusy, setSnapshotBusy] = useState(false);

  const blobUrlRef = useRef<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  const replaceFrameUrl = useCallback((nextUrl: string | null) => {
    const prev = blobUrlRef.current;
    blobUrlRef.current = nextUrl;
    setFrameUrl(nextUrl);
    if (prev && prev !== nextUrl) {
      URL.revokeObjectURL(prev);
    }
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const nextStatus = await apiClient.getCameraStatus();
      setStatus(nextStatus);
      setLiveCameraName(nextStatus.camera_name);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load camera status.");
    } finally {
      setLoading(false);
    }
  }, []);

  const takeSnapshot = useCallback(async () => {
    setSnapshotBusy(true);
    setStreamError(null);
    try {
      const url = await apiClient.fetchLiveSnapshotBlobUrl();
      replaceFrameUrl(url);
      setLastUpdatedAt(new Date().toLocaleTimeString());
    } catch (err) {
      setStreamError(err instanceof ApiError ? err.message : "Failed to capture live snapshot.");
    } finally {
      setSnapshotBusy(false);
    }
  }, [replaceFrameUrl]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    return () => {
      if (blobUrlRef.current) {
        URL.revokeObjectURL(blobUrlRef.current);
        blobUrlRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    if (paused) {
      setStreamState("paused");
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
      return;
    }

    let cancelled = false;
    let reconnectTimer: number | undefined;
    let attempt = 0;

    const connect = async () => {
      if (cancelled) {
        return;
      }
      setStreamState("connecting");

      // Refresh status/session via authorizedFetch before opening WS in case the short-lived access token expired.
      try {
        const refreshedStatus = await apiClient.getCameraStatus();
        if (!cancelled) {
          setStatus(refreshedStatus);
          setLiveCameraName(refreshedStatus.camera_name);
        }
      } catch (err) {
        if (!cancelled && err instanceof ApiError && err.status === 401) {
          setStreamState("offline");
          setStreamError("Authentication expired. Please sign in again.");
          return;
        }
      }

      if (cancelled) {
        return;
      }

      const token = getAccessToken();
      if (!token) {
        setStreamState("offline");
        setStreamError("Missing access token for live stream.");
        return;
      }

      const ws = new WebSocket(buildWsLiveUrl(token));
      ws.binaryType = "blob";
      wsRef.current = ws;

      ws.onmessage = (event) => {
        if (cancelled) {
          return;
        }
        if (typeof event.data === "string") {
          try {
            const msg = JSON.parse(event.data) as LiveStatusPayload;
            if (msg.type === "status") {
              if (msg.camera_name) {
                setLiveCameraName(msg.camera_name);
              }
              if (typeof msg.fps === "number") {
                setLiveFps(msg.fps);
              }
              setStreamError(msg.error ?? null);
              if (msg.status === "connected") {
                attempt = 0;
                setStreamState("connected");
              } else if (msg.status === "disconnected") {
                setStreamState("offline");
              } else {
                setStreamState("connecting");
              }
            }
          } catch {
            // Ignore malformed status frame
          }
          return;
        }

        const blob =
          event.data instanceof Blob
            ? event.data
            : new Blob([event.data as ArrayBuffer], { type: "image/jpeg" });
        const nextUrl = URL.createObjectURL(blob);
        replaceFrameUrl(nextUrl);
        attempt = 0;
        setStreamState("connected");
        setStreamError(null);
        setLastUpdatedAt(new Date().toLocaleTimeString());
      };

      ws.onerror = () => {
        if (!cancelled) {
          setStreamError("Live stream connection interrupted.");
        }
      };

      ws.onclose = () => {
        if (wsRef.current === ws) {
          wsRef.current = null;
        }
        if (cancelled) {
          return;
        }
        setStreamState("offline");
        const delayMs = Math.min(8000, 1000 * Math.pow(2, attempt));
        attempt += 1;
        reconnectTimer = window.setTimeout(() => {
          void connect();
        }, delayMs);
      };
    };

    void connect();

    return () => {
      cancelled = true;
      if (reconnectTimer !== undefined) {
        window.clearTimeout(reconnectTimer);
      }
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [paused, replaceFrameUrl]);

  const effectiveName = liveCameraName ?? status?.camera_name ?? "Camera";
  const statusChipVariant =
    streamState === "connected"
      ? "success"
      : streamState === "offline"
      ? "danger"
      : "warning";

  return (
    <div className="page-container">
      <div className="page-header">
        <div>
          <h1 className="page-title">Live Camera</h1>
          <p className="page-subtitle">
            Authenticated local camera stream over WebSocket ({effectiveName}).
          </p>
        </div>
        <div className="row-wrap">
          <Button
            variant={paused ? "primary" : "secondary"}
            size="sm"
            onClick={() => setPaused((prev) => !prev)}
          >
            {paused ? "Resume Stream" : "Pause Stream"}
          </Button>
          <Button
            variant="secondary"
            size="sm"
            loading={snapshotBusy}
            onClick={() => void takeSnapshot()}
          >
            Snapshot
          </Button>
          <Button variant="secondary" size="sm" onClick={() => void load()}>
            Refresh Status
          </Button>
        </div>
      </div>

      {error ? (
        <GlassCard>
          <div className="error-banner" role="alert">
            {error}
          </div>
        </GlassCard>
      ) : null}

      {streamError ? (
        <GlassCard>
          <div className="error-banner" role="alert">
            {streamError}
          </div>
        </GlassCard>
      ) : null}

      <GlassCard variant="elevated">
        {loading && !status ? (
          <Skeleton variant="card" height={320} />
        ) : !status ? (
          <EmptyState
            title="No Camera Status Available"
            description="Start the surveillance backend or check connectivity."
            action={
              <Button variant="primary" onClick={() => void load()}>
                Retry
              </Button>
            }
          />
        ) : (
          <div className="stack-md">
            <div className="row-wrap">
              <Chip
                label={`${effectiveName}: ${streamState.toUpperCase()}`}
                variant={statusChipVariant}
                dot
              />
              <Chip
                label={liveFps !== null ? `Stream FPS: ${liveFps.toFixed(1)}` : "Stream FPS: --"}
                variant="neutral"
              />
              {lastUpdatedAt ? (
                <Chip label={`Last frame: ${lastUpdatedAt}`} variant="neutral" />
              ) : null}
            </div>

            {frameUrl ? (
              <div className="live-video-frame">
                <img
                  src={frameUrl}
                  alt={`${effectiveName} live feed`}
                  className="live-video-img"
                />
              </div>
            ) : (
              <div className="live-placeholder-box">
                <svg
                  width="42"
                  height="42"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M23 7l-7 5 7 5V7z" />
                  <rect x="1" y="5" width="15" height="14" rx="2" />
                </svg>
                <h2 style={{ margin: 0, fontSize: "1.15rem" }}>
                  {paused
                    ? "Live Stream Paused"
                    : streamState === "offline"
                    ? "Waiting for Camera Connection..."
                    : "Opening Built-in Camera..."}
                </h2>
                <p style={{ margin: 0, maxWidth: "46ch", opacity: 0.9 }}>
                  {paused
                    ? "Click Resume Stream to reconnect your browser to the local camera feed."
                    : "The backend opens your configured camera when viewers connect (DirectShow startup on Windows can take a few seconds)."}
                </p>
              </div>
            )}

            <div className="row-wrap">
              <Button variant="primary" onClick={() => onNavigate("/events")}>
                Browse Snapshots
              </Button>
              <Button variant="secondary" onClick={() => onNavigate("/zones")}>
                Edit Zones
              </Button>
            </div>
          </div>
        )}
      </GlassCard>
    </div>
  );
};

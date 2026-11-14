import React, { useCallback, useEffect, useRef, useState } from "react";
import { apiClient, ApiError } from "../api/client";
import type { ZonePayload } from "../api/types";
import {
  Button,
  Chip,
  EmptyState,
  GlassCard,
  Input,
  Skeleton,
  type ToastData,
} from "../components/ui";

export interface ZonesPageProps {
  onNotify: (toast: Omit<ToastData, "id">) => void;
}

const DEFAULT_CANVAS_WIDTH = 960;
const DEFAULT_CANVAS_HEIGHT = 540;

const ZONE_PALETTE: [number, number, number][] = [
  [41, 82, 85],
  [217, 119, 6],
  [22, 163, 74],
  [180, 35, 24],
  [37, 99, 235],
];

function slugifyZoneId(name: string, existingIds: Set<string>): string {
  const base =
    name
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/^_+|_+$/g, "") || "zone";
  if (!existingIds.has(base)) return base;
  let suffix = 2;
  while (existingIds.has(`${base}_${suffix}`)) {
    suffix += 1;
  }
  return `${base}_${suffix}`;
}

export const ZonesPage: React.FC<ZonesPageProps> = ({ onNotify }) => {
  const [zones, setZones] = useState<ZonePayload[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [draftPoints, setDraftPoints] = useState<[number, number][]>([]);
  const [zoneName, setZoneName] = useState("");
  const [dwellSeconds, setDwellSeconds] = useState("2");
  const [zoneEnabled, setZoneEnabled] = useState(true);

  const [bgImage, setBgImage] = useState<HTMLImageElement | null>(null);
  const [bgSourceLabel, setBgSourceLabel] = useState<string>("Calibrated grid (no snapshot)");
  const [snapshotLoading, setSnapshotLoading] = useState(false);

  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const activeObjectUrlRef = useRef<string | null>(null);

  const revokeObjectUrl = () => {
    if (activeObjectUrlRef.current) {
      URL.revokeObjectURL(activeObjectUrlRef.current);
      activeObjectUrlRef.current = null;
    }
  };

  const loadImageFromUrl = useCallback((url: string, label: string, shouldRevoke: boolean) => {
    const img = new Image();
    img.onload = () => {
      if (shouldRevoke && activeObjectUrlRef.current && activeObjectUrlRef.current !== url) {
        URL.revokeObjectURL(activeObjectUrlRef.current);
      }
      if (shouldRevoke) {
        activeObjectUrlRef.current = url;
      }
      setBgImage(img);
      setBgSourceLabel(label);
    };
    img.onerror = () => {
      if (shouldRevoke) {
        URL.revokeObjectURL(url);
      }
    };
    img.src = url;
  }, []);

  const loadLatestSnapshot = useCallback(async () => {
    setSnapshotLoading(true);
    try {
      const evRes = await apiClient.getEvents({ limit: 30 });
      const withSnap = evRes.events.find((ev) => Boolean(ev.snapshot_url));
      if (withSnap) {
        const blobUrl = await apiClient.fetchSnapshotBlobUrl(withSnap.id);
        loadImageFromUrl(
          blobUrl,
          `Event #${withSnap.id} (${new Date(withSnap.created_at).toLocaleString()})`,
          true
        );
      }
    } catch {
      // Keep grid fallback silently if no snapshots or transient fetch issue
    } finally {
      setSnapshotLoading(false);
    }
  }, [loadImageFromUrl]);

  const loadZones = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await apiClient.getZones();
      setZones(resp.zones);
      setDirty(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load zones.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadZones();
    void loadLatestSnapshot();
    return () => {
      revokeObjectUrl();
    };
  }, [loadZones, loadLatestSnapshot]);

  const canvasWidth = bgImage?.naturalWidth || DEFAULT_CANVAS_WIDTH;
  const canvasHeight = bgImage?.naturalHeight || DEFAULT_CANVAS_HEIGHT;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    ctx.clearRect(0, 0, canvasWidth, canvasHeight);

    if (bgImage) {
      ctx.drawImage(bgImage, 0, 0, canvasWidth, canvasHeight);
    } else {
      ctx.fillStyle = "#162623";
      ctx.fillRect(0, 0, canvasWidth, canvasHeight);
      ctx.strokeStyle = "rgba(240, 245, 247, 0.12)";
      ctx.lineWidth = 1;
      for (let x = 60; x < canvasWidth; x += 60) {
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, canvasHeight);
        ctx.stroke();
      }
      for (let y = 60; y < canvasHeight; y += 60) {
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(canvasWidth, y);
        ctx.stroke();
      }
      ctx.fillStyle = "rgba(240, 245, 247, 0.55)";
      ctx.font = "600 16px system-ui, sans-serif";
      ctx.fillText("Click or tap points to draw a restricted zone", 24, 36);
    }

    zones.forEach((zone) => {
      if (zone.points.length < 3) return;
      const [r, g, b] = zone.color?.length === 3 ? zone.color : [41, 82, 85];
      ctx.beginPath();
      zone.points.forEach(([px, py], idx) => {
        if (idx === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      });
      ctx.closePath();
      ctx.fillStyle = zone.enabled
        ? `rgba(${r}, ${g}, ${b}, 0.28)`
        : "rgba(148, 163, 184, 0.16)";
      ctx.fill();
      ctx.lineWidth = zone.enabled ? 3 : 2;
      ctx.strokeStyle = zone.enabled ? `rgb(${r}, ${g}, ${b})` : "#94a3b8";
      ctx.stroke();

      const [lx, ly] = zone.points[0];
      ctx.fillStyle = "rgba(22, 38, 35, 0.85)";
      ctx.fillRect(lx, Math.max(4, ly - 26), Math.max(90, zone.name.length * 9 + 18), 24);
      ctx.fillStyle = "#F0F5F7";
      ctx.font = "600 13px system-ui, sans-serif";
      ctx.fillText(
        `${zone.name}${zone.enabled ? "" : " (off)"}`,
        lx + 8,
        Math.max(20, ly - 10)
      );
    });

    if (draftPoints.length > 0) {
      ctx.beginPath();
      draftPoints.forEach(([px, py], idx) => {
        if (idx === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      });
      if (draftPoints.length >= 3) {
        ctx.closePath();
        ctx.fillStyle = "rgba(41, 82, 85, 0.25)";
        ctx.fill();
      }
      ctx.lineWidth = 3;
      ctx.strokeStyle = "#38bdf8";
      ctx.stroke();

      draftPoints.forEach(([px, py], idx) => {
        ctx.beginPath();
        ctx.arc(px, py, 6, 0, Math.PI * 2);
        ctx.fillStyle = idx === 0 ? "#f59e0b" : "#38bdf8";
        ctx.fill();
        ctx.strokeStyle = "#ffffff";
        ctx.lineWidth = 2;
        ctx.stroke();
      });
    }
  }, [bgImage, canvasHeight, canvasWidth, draftPoints, zones]);

  const handleCanvasPointerDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;

    const scaleX = canvasWidth / rect.width;
    const scaleY = canvasHeight / rect.height;
    const x = Math.max(0, Math.min(canvasWidth, Math.round((e.clientX - rect.left) * scaleX)));
    const y = Math.max(0, Math.min(canvasHeight, Math.round((e.clientY - rect.top) * scaleY)));

    setDraftPoints((prev) => [...prev, [x, y]]);
  };

  const handleUploadSnapshot = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const url = URL.createObjectURL(file);
    loadImageFromUrl(url, `Local file: ${file.name}`, true);
  };

  const handleAddZone = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!zoneName.trim()) {
      setError("Enter a zone name before adding the polygon.");
      return;
    }
    if (draftPoints.length < 3) {
      setError("Click at least 3 points on the canvas to form a polygon.");
      return;
    }
    const existingIds = new Set(zones.map((z) => z.id));
    const color = ZONE_PALETTE[zones.length % ZONE_PALETTE.length];
    const parsedDwell = Math.max(0, Number(dwellSeconds) || 0);

    const newZone: ZonePayload = {
      id: slugifyZoneId(zoneName.trim(), existingIds),
      name: zoneName.trim(),
      enabled: zoneEnabled,
      dwell_seconds: parsedDwell,
      points: draftPoints,
      color,
    };

    setZones((prev) => [...prev, newZone]);
    setDraftPoints([]);
    setZoneName("");
    setDwellSeconds("2");
    setZoneEnabled(true);
    setDirty(true);
  };

  const handleToggleZone = (zoneId: string) => {
    setZones((prev) =>
      prev.map((z) => (z.id === zoneId ? { ...z, enabled: !z.enabled } : z))
    );
    setDirty(true);
  };

  const handleRemoveZone = (zoneId: string) => {
    setZones((prev) => prev.filter((z) => z.id !== zoneId));
    setDirty(true);
  };

  const handleSaveZones = async () => {
    setSaving(true);
    setError(null);
    try {
      const resp = await apiClient.saveZones(zones);
      setZones(resp.zones);
      setDirty(false);
      onNotify({
        title: "Zones saved",
        description: `${resp.zones.length} zone(s) written to config/zones.json.`,
        tone: "success",
      });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to save zones.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="page-container">
      <header className="page-header">
        <div>
          <h1 className="page-title">Zones</h1>
          <p className="page-subtitle">
            Draw polygon intrusion zones over a camera snapshot ({canvasWidth}x{canvasHeight}).
          </p>
        </div>
        <div className="row-wrap">
          {dirty ? <Chip label="Unsaved changes" variant="warning" dot /> : null}
          <Button
            variant="secondary"
            onClick={() => void loadLatestSnapshot()}
            loading={snapshotLoading}
          >
            Load Latest Snapshot
          </Button>
          <Button
            variant="primary"
            onClick={() => void handleSaveZones()}
            loading={saving}
            disabled={loading}
          >
            Save Zones
          </Button>
        </div>
      </header>

      {error ? (
        <div className="error-banner row-wrap" role="alert">
          <span>{error}</span>
          <Button size="sm" variant="secondary" onClick={() => setError(null)}>
            Dismiss
          </Button>
        </div>
      ) : null}

      {loading ? (
        <GlassCard>
          <Skeleton variant="card" height={340} />
        </GlassCard>
      ) : (
        <div className="stack-md">
          <GlassCard>
            <div className="stack-md">
              <div className="page-header">
                <span className="page-subtitle">Background: {bgSourceLabel}</span>
                <label className="row-wrap" style={{ fontSize: "0.85rem" }}>
                  <span>Reference image:</span>
                  <input
                    type="file"
                    accept="image/*"
                    onChange={handleUploadSnapshot}
                    aria-label="Upload reference snapshot"
                  />
                </label>
              </div>

              <div className="zone-canvas-wrap">
                <canvas
                  ref={canvasRef}
                  width={canvasWidth}
                  height={canvasHeight}
                  className="zone-canvas"
                  onPointerDown={handleCanvasPointerDown}
                />
              </div>

              <form className="stack-md" onSubmit={handleAddZone}>
                <div className="filter-grid">
                  <Input
                    label="New Zone Name"
                    placeholder="e.g., Front Porch"
                    value={zoneName}
                    onChange={(e) => setZoneName(e.target.value)}
                  />
                  <Input
                    label="Dwell Threshold (seconds)"
                    type="number"
                    min={0}
                    step="0.5"
                    value={dwellSeconds}
                    onChange={(e) => setDwellSeconds(e.target.value)}
                  />
                  <div style={{ display: "flex", alignItems: "flex-end" }}>
                    <label className="checkbox-row">
                      <input
                        type="checkbox"
                        checked={zoneEnabled}
                        onChange={(e) => setZoneEnabled(e.target.checked)}
                      />
                      <span>Enabled immediately</span>
                    </label>
                  </div>
                </div>

                <div className="row-wrap">
                  <Chip label={`${draftPoints.length} draft point(s)`} variant="accent" />
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    disabled={draftPoints.length === 0}
                    onClick={() => setDraftPoints((prev) => prev.slice(0, -1))}
                  >
                    Undo Point
                  </Button>
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    disabled={draftPoints.length === 0}
                    onClick={() => setDraftPoints([])}
                  >
                    Clear Draft
                  </Button>
                  <Button type="submit" variant="primary" size="sm">
                    Add Polygon to List
                  </Button>
                </div>
              </form>
            </div>
          </GlassCard>

          {zones.length === 0 ? (
            <GlassCard>
              <EmptyState
                title="No zones configured"
                description="Click at least three vertices on the canvas above, give the zone a name, and save to activate intrusion detection."
              />
            </GlassCard>
          ) : (
            <div className="grid-cards grid-cards--2">
              {zones.map((zone) => (
                <GlassCard key={zone.id}>
                  <div className="stack-sm">
                    <div className="page-header">
                      <strong>{zone.name}</strong>
                      <Chip
                        label={zone.enabled ? "Enabled" : "Disabled"}
                        variant={zone.enabled ? "success" : "neutral"}
                        dot
                      />
                    </div>
                    <p className="page-subtitle">
                      ID: {zone.id} · {zone.points.length} vertices · Dwell:{" "}
                      {zone.dwell_seconds}s
                    </p>
                    <div className="row-wrap" style={{ justifyContent: "flex-end" }}>
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={() => handleToggleZone(zone.id)}
                      >
                        {zone.enabled ? "Disable" : "Enable"}
                      </Button>
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={() => handleRemoveZone(zone.id)}
                      >
                        Remove
                      </Button>
                    </div>
                  </div>
                </GlassCard>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
};

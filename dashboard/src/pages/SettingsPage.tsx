import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  apiClient,
  ApiError,
  getCustomApiUrl,
  setCustomApiUrl,
} from "../api/client";
import type { CameraStatus, SettingsResponse, ZonePayload } from "../api/types";
import {
  Button,
  Chip,
  EmptyState,
  GlassCard,
  Input,
  Skeleton,
  type ToastData,
} from "../components/ui";
import { useAuth } from "../context/AuthContext";

export interface SettingsPageProps {
  onNotify: (toast: Omit<ToastData, "id">) => void;
}

interface SettingsFormState {
  camera_name: string;
  detection_confidence: string;
  detection_target_fps: string;
  face_match_threshold: string;
  event_track_cooldown_seconds: string;
  event_zone_cooldown_seconds: string;
  retention_days: string;
  alert_on_unknown_person: boolean;
  alert_on_known_person: boolean;
  alert_on_zone_intrusion: boolean;
  alert_on_camera_offline: boolean;
  alert_on_camera_online: boolean;
  alert_on_system_error: boolean;
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

function toFormState(data: SettingsResponse): SettingsFormState {
  const ov = data.overrides ?? {};
  const boolVal = (key: string, fallback: boolean) => {
    if (key in ov) {
      return ["1", "true", "yes", "on"].includes(ov[key].toLowerCase());
    }
    return fallback;
  };

  return {
    camera_name: data.camera_name,
    detection_confidence: String(data.detection_confidence),
    detection_target_fps: String(data.detection_target_fps),
    face_match_threshold: String(data.face_match_threshold),
    event_track_cooldown_seconds: String(data.event_track_cooldown_seconds),
    event_zone_cooldown_seconds: String(data.event_zone_cooldown_seconds),
    retention_days: String(data.retention_days),
    alert_on_unknown_person: data.alert_on_unknown_person,
    alert_on_known_person: data.alert_on_known_person,
    alert_on_zone_intrusion: data.alert_on_zone_intrusion,
    alert_on_camera_offline: boolVal("alert_on_camera_offline", true),
    alert_on_camera_online: boolVal("alert_on_camera_online", true),
    alert_on_system_error: boolVal("alert_on_system_error", true),
  };
}

export const SettingsPage: React.FC<SettingsPageProps> = ({ onNotify }) => {
  const { user, logout } = useAuth();

  // Settings state
  const [settings, setSettings] = useState<SettingsResponse | null>(null);
  const [cameraStatus, setCameraStatus] = useState<CameraStatus | null>(null);
  const [form, setForm] = useState<SettingsFormState | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Connection config state
  const [customApiInput, setCustomApiInput] = useState<string>(() => getCustomApiUrl());

  // Zones state
  const [zones, setZones] = useState<ZonePayload[]>([]);
  const [zonesLoading, setZonesLoading] = useState(true);
  const [zonesSaving, setZonesSaving] = useState(false);
  const [zonesDirty, setZonesDirty] = useState(false);
  const [zoneName, setZoneName] = useState("");
  const [dwellSeconds, setDwellSeconds] = useState("2");
  const [zoneEnabled, setZoneEnabled] = useState(true);
  const [draftPoints, setDraftPoints] = useState<[number, number][]>([]);

  // Canvas background image
  const [bgImage, setBgImage] = useState<HTMLImageElement | null>(null);
  const [bgSourceLabel, setBgSourceLabel] = useState<string>("Calibrated grid (no snapshot)");
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
    img.src = url;
  }, []);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [settingsRes, camRes, zonesRes] = await Promise.all([
        apiClient.getSettings(),
        apiClient.getCameraStatus().catch(() => null),
        apiClient.getZones().catch(() => ({ zones: [] })),
      ]);
      setSettings(settingsRes);
      setForm(toFormState(settingsRes));
      if (camRes) setCameraStatus(camRes);
      setZones(zonesRes.zones);
      setZonesDirty(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load settings.");
    } finally {
      setLoading(false);
      setZonesLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadData();
    return () => {
      revokeObjectUrl();
    };
  }, [loadData]);

  // Load snapshot for zone canvas
  const loadLatestSnapshot = useCallback(async () => {
    try {
      const evRes = await apiClient.getEvents({ limit: 30 });
      const withSnap = evRes.events.find((ev) => Boolean(ev.snapshot_url));
      if (withSnap) {
        const blobUrl = await apiClient.fetchSnapshotBlobUrl(withSnap.id);
        loadImageFromUrl(blobUrl, `Event #${withSnap.id}`, true);
      }
    } catch {
      // ignore
    }
  }, [loadImageFromUrl]);

  useEffect(() => {
    void loadLatestSnapshot();
  }, [loadLatestSnapshot]);

  // Canvas drawing
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
      ctx.fillText("Click on the canvas to draw detection zone vertices", 24, 36);
    }

    // Existing zones
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
      ctx.fillText(`${zone.name}${zone.enabled ? "" : " (off)"}`, lx + 8, Math.max(20, ly - 10));
    });

    // Draft polygon
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
      ctx.strokeStyle = "#577877";
      ctx.stroke();

      draftPoints.forEach(([px, py], idx) => {
        ctx.beginPath();
        ctx.arc(px, py, 6, 0, Math.PI * 2);
        ctx.fillStyle = idx === 0 ? "#f59e0b" : "#577877";
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

  const handleAddZone = (e: React.FormEvent) => {
    e.preventDefault();
    if (!zoneName.trim() || draftPoints.length < 3) return;

    const existingIds = new Set(zones.map((z) => z.id));
    const color = ZONE_PALETTE[zones.length % ZONE_PALETTE.length];
    const newZone: ZonePayload = {
      id: slugifyZoneId(zoneName.trim(), existingIds),
      name: zoneName.trim(),
      enabled: zoneEnabled,
      dwell_seconds: Math.max(0, Number(dwellSeconds) || 0),
      points: draftPoints,
      color,
    };

    setZones((prev) => [...prev, newZone]);
    setDraftPoints([]);
    setZoneName("");
    setDwellSeconds("2");
    setZoneEnabled(true);
    setZonesDirty(true);
  };

  const handleSaveZones = async () => {
    setZonesSaving(true);
    try {
      const resp = await apiClient.saveZones(zones);
      setZones(resp.zones);
      setZonesDirty(false);
      onNotify({
        title: "Zones updated",
        description: `${resp.zones.length} zone(s) persisted.`,
        tone: "success",
      });
    } catch (err) {
      onNotify({
        title: "Zone save error",
        description: err instanceof ApiError ? err.message : "Failed to save zones.",
        tone: "danger",
      });
    } finally {
      setZonesSaving(false);
    }
  };

  const handleSaveSettings = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form) return;

    setSaving(true);
    setError(null);
    try {
      const overrides: Record<string, string> = {
        camera_name: form.camera_name.trim(),
        detection_confidence: form.detection_confidence,
        detection_target_fps: form.detection_target_fps,
        face_match_threshold: form.face_match_threshold,
        event_track_cooldown_seconds: form.event_track_cooldown_seconds,
        event_zone_cooldown_seconds: form.event_zone_cooldown_seconds,
        retention_days: form.retention_days,
        alert_on_unknown_person: String(form.alert_on_unknown_person),
        alert_on_known_person: String(form.alert_on_known_person),
        alert_on_zone_intrusion: String(form.alert_on_zone_intrusion),
        alert_on_camera_offline: String(form.alert_on_camera_offline),
        alert_on_camera_online: String(form.alert_on_camera_online),
        alert_on_system_error: String(form.alert_on_system_error),
      };

      const updated = await apiClient.updateSettings(overrides);
      setSettings(updated);
      setForm(toFormState(updated));
      onNotify({
        title: "Settings saved",
        description: "Configuration changes applied immediately.",
        tone: "success",
      });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to save settings.");
    } finally {
      setSaving(false);
    }
  };

  const handleSaveApiUrl = () => {
    setCustomApiUrl(customApiInput.trim() || null);
    onNotify({
      title: "API URL updated",
      description: customApiInput.trim()
        ? `Configured: ${customApiInput.trim()}`
        : "Restored default relative /api route",
      tone: "info",
    });
  };

  const isOnline = cameraStatus?.status === "online";

  return (
    <div className="lg-settings-page">
      <header className="lg-page-header">
        <div>
          <h1 className="lg-page-title">Settings</h1>
          <p className="lg-page-subtitle">Camera parameters, detection zones, and alert policies</p>
        </div>
        <Button variant="secondary" onClick={() => void loadData()} disabled={loading}>
          Reload
        </Button>
      </header>

      {error && (
        <div className="lg-error-banner" role="alert">
          <span>{error}</span>
          <Button size="sm" variant="secondary" onClick={() => void loadData()}>
            Dismiss
          </Button>
        </div>
      )}

      {loading || !settings || !form ? (
        <div className="lg-settings-skeleton">
          <Skeleton variant="card" height={160} />
          <Skeleton variant="card" height={300} />
          <Skeleton variant="card" height={160} />
        </div>
      ) : (
        <div className="lg-settings-content">
          {/* 1. Camera Source & Status */}
          <GlassCard className="lg-settings-section">
            <div className="lg-section-header">
              <div>
                <h2 className="lg-section-title">Camera & Stream</h2>
                <p className="lg-section-subtitle">Active video capture device and feed health</p>
              </div>
              <div className="lg-chip-row">
                <Chip
                  label={isOnline ? "Camera Online" : "Camera Offline"}
                  variant={isOnline ? "success" : "danger"}
                  dot
                />
              </div>
            </div>

            <div className="lg-camera-stats-grid">
              <div className="lg-stat-box">
                <span className="lg-stat-label">Camera Identifier</span>
                <strong className="lg-stat-value">{settings.camera_name}</strong>
              </div>
              <div className="lg-stat-box">
                <span className="lg-stat-label">Input Resolution</span>
                <strong className="lg-stat-value">{settings.detection_imgsz}px</strong>
              </div>
              <div className="lg-stat-box">
                <span className="lg-stat-label">Motion Gate</span>
                <strong className="lg-stat-value">
                  {settings.motion_gate_enabled ? "Enabled" : "Disabled"}
                </strong>
              </div>
              <div className="lg-stat-box">
                <span className="lg-stat-label">Total Events Logged</span>
                <strong className="lg-stat-value">{cameraStatus?.total_events ?? 0}</strong>
              </div>
            </div>
          </GlassCard>

          {/* 2. Detection Zones (Interactive Canvas Editor) */}
          <GlassCard className="lg-settings-section">
            <div className="lg-section-header">
              <div>
                <h2 className="lg-section-title">Detection Zones</h2>
                <p className="lg-section-subtitle">
                  Define restricted polygon perimeters on the camera field of view
                </p>
              </div>
              <div className="lg-chip-row">
                {zonesDirty && <Chip label="Unsaved Changes" variant="warning" dot />}
                <Button
                  variant="primary"
                  size="sm"
                  onClick={() => void handleSaveZones()}
                  loading={zonesSaving}
                  disabled={zonesLoading}
                >
                  Save Zones
                </Button>
              </div>
            </div>

            <div className="lg-canvas-editor-block">
              <div className="lg-canvas-meta-bar">
                <span className="lg-canvas-meta-label">Canvas source: {bgSourceLabel}</span>
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => void loadLatestSnapshot()}
                >
                  Refresh Snapshot
                </Button>
              </div>

              <div className="lg-canvas-container">
                <canvas
                  ref={canvasRef}
                  width={canvasWidth}
                  height={canvasHeight}
                  className="lg-zone-canvas"
                  onPointerDown={handleCanvasPointerDown}
                />
              </div>

              <form onSubmit={handleAddZone} className="lg-zone-form">
                <div className="lg-zone-form-inputs">
                  <Input
                    label="Zone Name"
                    placeholder="e.g. Front Door"
                    value={zoneName}
                    onChange={(e) => setZoneName(e.target.value)}
                  />
                  <Input
                    label="Dwell Threshold (seconds)"
                    type="number"
                    min="0"
                    step="0.5"
                    value={dwellSeconds}
                    onChange={(e) => setDwellSeconds(e.target.value)}
                  />
                  <label className="lg-checkbox-row" style={{ marginTop: "auto", paddingBottom: 8 }}>
                    <input
                      type="checkbox"
                      checked={zoneEnabled}
                      onChange={(e) => setZoneEnabled(e.target.checked)}
                    />
                    <span>Active immediately</span>
                  </label>
                </div>

                <div className="lg-zone-form-actions">
                  <Chip label={`${draftPoints.length} vertices drafted`} variant="accent" />
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    disabled={draftPoints.length === 0}
                    onClick={() => setDraftPoints((prev) => prev.slice(0, -1))}
                  >
                    Undo Vertex
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
                  <Button
                    type="submit"
                    variant="primary"
                    size="sm"
                    disabled={draftPoints.length < 3 || !zoneName.trim()}
                  >
                    Add Zone to List
                  </Button>
                </div>
              </form>

              {zones.length === 0 ? (
                <EmptyState
                  title="No zones defined"
                  description="Click at least three vertices on the canvas to outline an intrusion zone."
                />
              ) : (
                <div className="lg-zones-list">
                  {zones.map((zone) => (
                    <div key={zone.id} className="lg-zone-chip-card">
                      <div className="lg-zone-info">
                        <strong>{zone.name}</strong>
                        <span className="lg-zone-sub">
                          {zone.points.length} points · {zone.dwell_seconds}s dwell
                        </span>
                      </div>
                      <div className="lg-zone-actions">
                        <Button
                          size="sm"
                          variant="secondary"
                          onClick={() => {
                            setZones((prev) =>
                              prev.map((z) => (z.id === zone.id ? { ...z, enabled: !z.enabled } : z))
                            );
                            setZonesDirty(true);
                          }}
                        >
                          {zone.enabled ? "Disable" : "Enable"}
                        </Button>
                        <Button
                          size="sm"
                          variant="secondary"
                          onClick={() => {
                            setZones((prev) => prev.filter((z) => z.id !== zone.id));
                            setZonesDirty(true);
                          }}
                        >
                          Remove
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </GlassCard>

          {/* 3. Alert Rules */}
          <GlassCard className="lg-settings-section">
            <div className="lg-section-header">
              <div>
                <h2 className="lg-section-title">Notification & Alert Rules</h2>
                <p className="lg-section-subtitle">
                  Configure real-time Telegram dispatch triggers
                </p>
              </div>
              <div className="lg-chip-row">
                <Chip
                  label={settings.telegram_configured ? "Bot Connected" : "Bot Token Missing"}
                  variant={settings.telegram_configured ? "success" : "warning"}
                />
              </div>
            </div>

            <form onSubmit={handleSaveSettings} className="lg-settings-form">
              <div className="lg-checkbox-grid">
                <label className="lg-checkbox-row">
                  <input
                    type="checkbox"
                    checked={form.alert_on_unknown_person}
                    onChange={(e) =>
                      setForm((prev) =>
                        prev ? { ...prev, alert_on_unknown_person: e.target.checked } : prev
                      )
                    }
                  />
                  <span>Dispatch alert on Unknown Person detection</span>
                </label>

                <label className="lg-checkbox-row">
                  <input
                    type="checkbox"
                    checked={form.alert_on_zone_intrusion}
                    onChange={(e) =>
                      setForm((prev) =>
                        prev ? { ...prev, alert_on_zone_intrusion: e.target.checked } : prev
                      )
                    }
                  />
                  <span>Dispatch alert on Zone Intrusion breach</span>
                </label>

                <label className="lg-checkbox-row">
                  <input
                    type="checkbox"
                    checked={form.alert_on_known_person}
                    onChange={(e) =>
                      setForm((prev) =>
                        prev ? { ...prev, alert_on_known_person: e.target.checked } : prev
                      )
                    }
                  />
                  <span>Dispatch alert for Known family members (uncheck for quiet logging)</span>
                </label>

                <label className="lg-checkbox-row">
                  <input
                    type="checkbox"
                    checked={form.alert_on_camera_offline}
                    onChange={(e) =>
                      setForm((prev) =>
                        prev ? { ...prev, alert_on_camera_offline: e.target.checked } : prev
                      )
                    }
                  />
                  <span>Dispatch alert if camera connection is lost</span>
                </label>

                <label className="lg-checkbox-row">
                  <input
                    type="checkbox"
                    checked={form.alert_on_camera_online}
                    onChange={(e) =>
                      setForm((prev) =>
                        prev ? { ...prev, alert_on_camera_online: e.target.checked } : prev
                      )
                    }
                  />
                  <span>Dispatch alert when camera recovers connection</span>
                </label>

                <label className="lg-checkbox-row">
                  <input
                    type="checkbox"
                    checked={form.alert_on_system_error}
                    onChange={(e) =>
                      setForm((prev) =>
                        prev ? { ...prev, alert_on_system_error: e.target.checked } : prev
                      )
                    }
                  />
                  <span>Dispatch alert on internal system exceptions</span>
                </label>
              </div>

              {/* 4. Advanced Options (Collapsed by default) */}
              <details className="lg-details-dropdown">
                <summary className="lg-details-summary">
                  Advanced Tuning (Confidence, Cooldowns & Retention)
                </summary>

                <div className="lg-advanced-grid">
                  <Input
                    label="Camera Name"
                    value={form.camera_name}
                    onChange={(e) =>
                      setForm((prev) =>
                        prev ? { ...prev, camera_name: e.target.value } : prev
                      )
                    }
                  />
                  <Input
                    label="Target Detection FPS"
                    type="number"
                    min={1}
                    max={30}
                    value={form.detection_target_fps}
                    onChange={(e) =>
                      setForm((prev) =>
                        prev ? { ...prev, detection_target_fps: e.target.value } : prev
                      )
                    }
                  />
                  <Input
                    label="Person Confidence Threshold"
                    type="number"
                    min={0.05}
                    max={0.99}
                    step={0.05}
                    value={form.detection_confidence}
                    onChange={(e) =>
                      setForm((prev) =>
                        prev ? { ...prev, detection_confidence: e.target.value } : prev
                      )
                    }
                  />
                  <Input
                    label="Face Match Threshold"
                    type="number"
                    min={0.1}
                    max={0.99}
                    step={0.01}
                    value={form.face_match_threshold}
                    onChange={(e) =>
                      setForm((prev) =>
                        prev ? { ...prev, face_match_threshold: e.target.value } : prev
                      )
                    }
                  />
                  <Input
                    label="Track Event Cooldown (sec)"
                    type="number"
                    min={0}
                    value={form.event_track_cooldown_seconds}
                    onChange={(e) =>
                      setForm((prev) =>
                        prev ? { ...prev, event_track_cooldown_seconds: e.target.value } : prev
                      )
                    }
                  />
                  <Input
                    label="Zone Event Cooldown (sec)"
                    type="number"
                    min={0}
                    value={form.event_zone_cooldown_seconds}
                    onChange={(e) =>
                      setForm((prev) =>
                        prev ? { ...prev, event_zone_cooldown_seconds: e.target.value } : prev
                      )
                    }
                  />
                  <Input
                    label="Snapshot Retention (days)"
                    type="number"
                    min={1}
                    max={365}
                    value={form.retention_days}
                    onChange={(e) =>
                      setForm((prev) =>
                        prev ? { ...prev, retention_days: e.target.value } : prev
                      )
                    }
                  />
                </div>
              </details>

              <div className="lg-form-submit-row">
                <Button type="submit" variant="primary" loading={saving}>
                  Save Settings
                </Button>
              </div>
            </form>
          </GlassCard>

          {/* 5. Account & Remote Connection */}
          <GlassCard className="lg-settings-section">
            <div className="lg-section-header">
              <div>
                <h2 className="lg-section-title">Account & Connection</h2>
                <p className="lg-section-subtitle">Authenticated session and backend endpoint</p>
              </div>
              <Button variant="secondary" size="sm" onClick={logout}>
                Sign Out
              </Button>
            </div>

            <div className="lg-account-details">
              <div className="lg-account-row">
                <span className="lg-account-label">User Account:</span>
                <span className="lg-account-value">{user?.username} ({user?.role})</span>
              </div>
              <div className="lg-account-row">
                <span className="lg-account-label">Backend Connection:</span>
                <div className="lg-api-url-group">
                  <Input
                    label="Custom API Base URL (Leave empty for default /api)"
                    placeholder="https://...trycloudflare.com/api"
                    value={customApiInput}
                    onChange={(e) => setCustomApiInput(e.target.value)}
                  />
                  <Button size="sm" variant="secondary" onClick={handleSaveApiUrl}>
                    Apply URL
                  </Button>
                </div>
              </div>
            </div>
          </GlassCard>
        </div>
      )}
    </div>
  );
};

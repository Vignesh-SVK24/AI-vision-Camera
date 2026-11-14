import React, { useCallback, useEffect, useState } from "react";
import { apiClient, ApiError } from "../api/client";
import type { SettingsResponse } from "../api/types";
import {
  Button,
  Chip,
  EmptyState,
  GlassCard,
  Input,
  Skeleton,
  type ToastData,
} from "../components/ui";

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
  const [settings, setSettings] = useState<SettingsResponse | null>(null);
  const [form, setForm] = useState<SettingsFormState | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadSettings = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await apiClient.getSettings();
      setSettings(resp);
      setForm(toFormState(resp));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load settings.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadSettings();
  }, [loadSettings]);

  const handleSave = async (e: React.FormEvent) => {
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
        description: "Runtime overrides were persisted to SQLite.",
        tone: "success",
      });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to save settings.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="page-container">
      <header className="page-header">
        <div>
          <h1 className="page-title">Settings</h1>
          <p className="page-subtitle">
            Tune detection thresholds, event cooldowns, retention policy, and alert rules.
          </p>
        </div>
        <Button variant="secondary" onClick={() => void loadSettings()} disabled={loading}>
          Reload
        </Button>
      </header>

      {error ? (
        <div className="error-banner row-wrap" role="alert">
          <span>{error}</span>
          <Button size="sm" variant="secondary" onClick={() => void loadSettings()}>
            Retry
          </Button>
        </div>
      ) : null}

      {loading ? (
        <div className="stack-md">
          <GlassCard>
            <Skeleton variant="card" height={180} />
          </GlassCard>
          <GlassCard>
            <Skeleton variant="card" height={180} />
          </GlassCard>
        </div>
      ) : !settings || !form ? (
        <GlassCard>
          <EmptyState
            title="Settings unavailable"
            description="Could not retrieve system settings from the backend API."
            action={
              <Button variant="primary" onClick={() => void loadSettings()}>
                Try Again
              </Button>
            }
          />
        </GlassCard>
      ) : (
        <form className="stack-md" onSubmit={handleSave}>
          <GlassCard>
            <div className="stack-md">
              <div className="page-header">
                <h2 style={{ margin: 0, fontSize: "1.1rem" }}>System Status</h2>
                <div className="row-wrap">
                  <Chip
                    label={`Telegram: ${settings.telegram_enabled ? "Enabled" : "Disabled"}`}
                    variant={settings.telegram_enabled ? "success" : "neutral"}
                  />
                  <Chip
                    label={
                      settings.telegram_configured
                        ? "Bot Token Configured"
                        : "Bot Token Missing"
                    }
                    variant={settings.telegram_configured ? "accent" : "warning"}
                  />
                  <Chip
                    label={`Motion Gate: ${settings.motion_gate_enabled ? "On" : "Off"}`}
                    variant="neutral"
                  />
                  <Chip label={`Input: ${settings.detection_imgsz}px`} variant="neutral" />
                </div>
              </div>
            </div>
          </GlassCard>

          <GlassCard>
            <div className="stack-md">
              <h2 style={{ margin: 0, fontSize: "1.1rem" }}>Detection & Retention</h2>

              <div className="grid-cards grid-cards--2">
                <Input
                  label="Camera Name"
                  value={form.camera_name}
                  onChange={(e) =>
                    setForm((prev) =>
                      prev ? { ...prev, camera_name: e.target.value } : prev
                    )
                  }
                  required
                />
                <Input
                  label="Target Detection FPS"
                  type="number"
                  min={1}
                  max={30}
                  step="1"
                  value={form.detection_target_fps}
                  onChange={(e) =>
                    setForm((prev) =>
                      prev ? { ...prev, detection_target_fps: e.target.value } : prev
                    )
                  }
                  required
                />
                <Input
                  label="Person Confidence Threshold (0.05 – 0.99)"
                  type="number"
                  min={0.05}
                  max={0.99}
                  step="0.05"
                  value={form.detection_confidence}
                  onChange={(e) =>
                    setForm((prev) =>
                      prev ? { ...prev, detection_confidence: e.target.value } : prev
                    )
                  }
                  required
                />
                <Input
                  label="Face Match Threshold (0.10 – 0.99)"
                  type="number"
                  min={0.1}
                  max={0.99}
                  step="0.01"
                  value={form.face_match_threshold}
                  onChange={(e) =>
                    setForm((prev) =>
                      prev ? { ...prev, face_match_threshold: e.target.value } : prev
                    )
                  }
                  required
                />
                <Input
                  label="Track Cooldown (seconds)"
                  type="number"
                  min={0}
                  step="5"
                  value={form.event_track_cooldown_seconds}
                  onChange={(e) =>
                    setForm((prev) =>
                      prev
                        ? { ...prev, event_track_cooldown_seconds: e.target.value }
                        : prev
                    )
                  }
                  required
                />
                <Input
                  label="Zone Cooldown (seconds)"
                  type="number"
                  min={0}
                  step="5"
                  value={form.event_zone_cooldown_seconds}
                  onChange={(e) =>
                    setForm((prev) =>
                      prev
                        ? { ...prev, event_zone_cooldown_seconds: e.target.value }
                        : prev
                    )
                  }
                  required
                />
                <Input
                  label="Event & Snapshot Retention (days)"
                  type="number"
                  min={1}
                  max={365}
                  step="1"
                  value={form.retention_days}
                  onChange={(e) =>
                    setForm((prev) =>
                      prev ? { ...prev, retention_days: e.target.value } : prev
                    )
                  }
                  required
                />
              </div>
            </div>
          </GlassCard>

          <GlassCard>
            <div className="stack-sm">
              <h2 style={{ margin: 0, fontSize: "1.1rem" }}>Alert Rules</h2>

              <label className="checkbox-row">
                <input
                  type="checkbox"
                  checked={form.alert_on_unknown_person}
                  onChange={(e) =>
                    setForm((prev) =>
                      prev ? { ...prev, alert_on_unknown_person: e.target.checked } : prev
                    )
                  }
                />
                <span>Alert on Unknown person detection (High priority)</span>
              </label>

              <label className="checkbox-row">
                <input
                  type="checkbox"
                  checked={form.alert_on_zone_intrusion}
                  onChange={(e) =>
                    setForm((prev) =>
                      prev ? { ...prev, alert_on_zone_intrusion: e.target.checked } : prev
                    )
                  }
                />
                <span>Alert on Zone intrusion (High priority)</span>
              </label>

              <label className="checkbox-row">
                <input
                  type="checkbox"
                  checked={form.alert_on_known_person}
                  onChange={(e) =>
                    setForm((prev) =>
                      prev ? { ...prev, alert_on_known_person: e.target.checked } : prev
                    )
                  }
                />
                <span>Send alerts for Known family members (off = quiet logging)</span>
              </label>

              <label className="checkbox-row">
                <input
                  type="checkbox"
                  checked={form.alert_on_camera_offline}
                  onChange={(e) =>
                    setForm((prev) =>
                      prev ? { ...prev, alert_on_camera_offline: e.target.checked } : prev
                    )
                  }
                />
                <span>Alert when camera goes offline</span>
              </label>

              <label className="checkbox-row">
                <input
                  type="checkbox"
                  checked={form.alert_on_camera_online}
                  onChange={(e) =>
                    setForm((prev) =>
                      prev ? { ...prev, alert_on_camera_online: e.target.checked } : prev
                    )
                  }
                />
                <span>Alert when camera comes back online</span>
              </label>

              <label className="checkbox-row">
                <input
                  type="checkbox"
                  checked={form.alert_on_system_error}
                  onChange={(e) =>
                    setForm((prev) =>
                      prev ? { ...prev, alert_on_system_error: e.target.checked } : prev
                    )
                  }
                />
                <span>Alert on system errors</span>
              </label>
            </div>
          </GlassCard>

          <div className="row-wrap" style={{ justifyContent: "flex-end" }}>
            <Button type="submit" variant="primary" size="lg" loading={saving}>
              Save Settings
            </Button>
          </div>
        </form>
      )}
    </div>
  );
};

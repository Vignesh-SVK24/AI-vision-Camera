export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

export interface User {
  id: number;
  username: string;
  role: string;
  is_active: boolean;
}

export interface CameraStatus {
  camera_name: string;
  status: "online" | "offline" | "unknown" | string;
  last_status_change: string | null;
  last_event_at: string | null;
  last_event_type: string | null;
  total_events: number;
}

export interface EventItem {
  id: number;
  event_type: string;
  created_at: string;
  track_id: number | null;
  zone_id: string | null;
  zone_name: string | null;
  person_id: number | null;
  person_name: string | null;
  confidence: number | null;
  snapshot_url: string | null;
  message: string | null;
  metadata: Record<string, unknown> | null;
}

export interface EventListResponse {
  events: EventItem[];
  count: number;
}

export interface PersonItem {
  id: number;
  name: string;
  is_trusted: boolean;
  consent_date: string | null;
  consent_note: string | null;
  embedding_count: number;
  photo_count: number;
  created_at: string;
}

export interface PersonEnrollResponse {
  person_id: number;
  name: string;
  enrolled_embeddings: number;
  rejected_reasons: Record<string, string>;
}

export interface ZonePayload {
  id: string;
  name: string;
  enabled: boolean;
  dwell_seconds: number;
  points: [number, number][];
  color: [number, number, number];
}

export interface ZonesResponse {
  zones: ZonePayload[];
}

export interface NotificationItem {
  id: number;
  event_id: number;
  event_type: string;
  event_created_at: string;
  notified: boolean;
  synced: boolean;
  notify_attempts: number;
  last_error: string | null;
  zone_name: string | null;
  message: string | null;
}

export interface SettingsResponse {
  app_name: string;
  camera_name: string;
  detection_confidence: number;
  detection_imgsz: number;
  detection_target_fps: number;
  motion_gate_enabled: boolean;
  face_match_threshold: number;
  event_track_cooldown_seconds: number;
  event_zone_cooldown_seconds: number;
  retention_days: number;
  telegram_enabled: boolean;
  telegram_configured: boolean;
  alert_on_unknown_person: boolean;
  alert_on_known_person: boolean;
  alert_on_zone_intrusion: boolean;
  overrides: Record<string, string>;
}

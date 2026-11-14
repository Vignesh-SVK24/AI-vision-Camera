import type {
  CameraStatus,
  EventListResponse,
  NotificationItem,
  PersonEnrollResponse,
  PersonItem,
  SettingsResponse,
  TokenResponse,
  User,
  ZonePayload,
  ZonesResponse,
} from "./types";

const ACCESS_TOKEN_KEY = "vs_access_token";
const REFRESH_TOKEN_KEY = "vs_refresh_token";
export const CUSTOM_API_URL_KEY = "vs_custom_api_url";

export function getApiBaseUrl(): string {
  const custom = typeof window !== "undefined" ? window.localStorage.getItem(CUSTOM_API_URL_KEY) : null;
  if (custom && custom.trim()) {
    const clean = custom.trim().replace(/\/$/, "");
    return clean.endsWith("/api") ? clean : `${clean}/api`;
  }
  return (
    import.meta.env.VITE_API_BASE_URL as string | undefined
  )?.replace(/\/$/, "") || "/api";
}

export function setCustomApiUrl(url: string | null): void {
  if (typeof window === "undefined") return;
  if (!url || !url.trim()) {
    window.localStorage.removeItem(CUSTOM_API_URL_KEY);
  } else {
    window.localStorage.setItem(CUSTOM_API_URL_KEY, url.trim().replace(/\/$/, ""));
  }
}

export function getCustomApiUrl(): string {
  return (typeof window !== "undefined" && window.localStorage.getItem(CUSTOM_API_URL_KEY)) || "";
}

export const API_BASE_URL = getApiBaseUrl();

export class ApiError extends Error {
  status: number;
  retryAfter?: number;

  constructor(message: string, status: number, retryAfter?: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

export function getAccessToken(): string | null {
  return window.localStorage.getItem(ACCESS_TOKEN_KEY);
}

export function getRefreshToken(): string | null {
  return window.localStorage.getItem(REFRESH_TOKEN_KEY);
}

export function saveTokens(tokens: TokenResponse): void {
  window.localStorage.setItem(ACCESS_TOKEN_KEY, tokens.access_token);
  window.localStorage.setItem(REFRESH_TOKEN_KEY, tokens.refresh_token);
}

export function clearTokens(): void {
  window.localStorage.removeItem(ACCESS_TOKEN_KEY);
  window.localStorage.removeItem(REFRESH_TOKEN_KEY);
}

function buildWsUrl(path: string, token: string): string {
  const custom = typeof window !== "undefined" ? window.localStorage.getItem(CUSTOM_API_URL_KEY) : null;
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  if (custom && custom.trim()) {
    const wsBase = custom.trim().replace(/^http/i, "ws").replace(/\/api$/, "");
    return `${wsBase}${normalizedPath}?token=${encodeURIComponent(token)}`;
  }
  const configured = import.meta.env.VITE_WS_BASE_URL as string | undefined;
  if (configured) {
    return `${configured.replace(/\/$/, "")}${normalizedPath}?token=${encodeURIComponent(token)}`;
  }
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}${normalizedPath}?token=${encodeURIComponent(token)}`;
}

export function buildWsEventsUrl(token: string): string {
  return buildWsUrl("/ws/events", token);
}

export function buildWsLiveUrl(token: string): string {
  return buildWsUrl("/ws/live", token);
}

async function parseErrorMessage(response: Response): Promise<string> {
  try {
    const data = await response.json();
    if (typeof data?.detail === "string") {
      return data.detail;
    }
    if (Array.isArray(data?.detail)) {
      return data.detail
        .map((item: { msg?: string; loc?: string[] }) =>
          item.loc ? `${item.loc.join(".")}: ${item.msg ?? "Invalid value"}` : item.msg
        )
        .filter(Boolean)
        .join("; ");
    }
  } catch {
    // Non-JSON error body; fall back to HTTP status text.
  }
  return response.statusText || `Request failed (${response.status})`;
}

let activeRefreshPromise: Promise<string | null> | null = null;
let onUnauthorizedHandler: (() => void) | null = null;

export function setUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorizedHandler = handler;
}

async function refreshAccessToken(): Promise<string | null> {
  if (activeRefreshPromise) {
    return activeRefreshPromise;
  }

  activeRefreshPromise = (async () => {
    const refresh = getRefreshToken();
    if (!refresh) {
      clearTokens();
      return null;
    }

    try {
      const resp = await fetch(`${getApiBaseUrl()}/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refresh }),
      });
      if (!resp.ok) {
        clearTokens();
        onUnauthorizedHandler?.();
        return null;
      }
      const tokens = (await resp.json()) as TokenResponse;
      saveTokens(tokens);
      return tokens.access_token;
    } catch {
      return null;
    } finally {
      activeRefreshPromise = null;
    }
  })();

  return activeRefreshPromise;
}

async function authorizedFetch(
  path: string,
  init: RequestInit = {},
  retryOn401 = true
): Promise<Response> {
  const token = getAccessToken();
  const headers = new Headers(init.headers);
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  let response: Response;
  try {
    response = await fetch(`${getApiBaseUrl()}${path}`, { ...init, headers });
  } catch {
    throw new ApiError(
      "Cannot reach backend API. Verify the server is running on localhost:8000.",
      0
    );
  }

  if (response.status === 401 && retryOn401 && getRefreshToken()) {
    const nextToken = await refreshAccessToken();
    if (nextToken) {
      const retryHeaders = new Headers(init.headers);
      retryHeaders.set("Authorization", `Bearer ${nextToken}`);
      response = await fetch(`${getApiBaseUrl()}${path}`, { ...init, headers: retryHeaders });
    }
  }

  if (!response.ok) {
    const msg = await parseErrorMessage(response);
    const retryAfterHeader = response.headers.get("Retry-After");
    const retryAfter = retryAfterHeader ? Number(retryAfterHeader) : undefined;
    if (response.status === 401) {
      clearTokens();
      onUnauthorizedHandler?.();
    }
    throw new ApiError(msg, response.status, retryAfter);
  }

  return response;
}

async function requestJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await authorizedFetch(path, init);
  return (await response.json()) as T;
}

export const apiClient = {
  async login(username: string, password: string): Promise<TokenResponse> {
    let response: Response;
    try {
      response = await fetch(`${getApiBaseUrl()}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
    } catch {
      throw new ApiError(
        "Cannot reach backend API. Verify the server is running on localhost:8000.",
        0
      );
    }

    if (!response.ok) {
      const msg = await parseErrorMessage(response);
      const retryAfterHeader = response.headers.get("Retry-After");
      throw new ApiError(
        msg,
        response.status,
        retryAfterHeader ? Number(retryAfterHeader) : undefined
      );
    }

    const tokens = (await response.json()) as TokenResponse;
    saveTokens(tokens);
    return tokens;
  },

  getMe(): Promise<User> {
    return requestJson<User>("/auth/me");
  },

  getCameraStatus(): Promise<CameraStatus> {
    return requestJson<CameraStatus>("/cameras/status");
  },

  getEvents(params: {
    startDate?: string;
    endDate?: string;
    eventType?: string;
    personName?: string;
    limit?: number;
  } = {}): Promise<EventListResponse> {
    const qs = new URLSearchParams();
    if (params.startDate) qs.set("start_date", params.startDate);
    if (params.endDate) qs.set("end_date", params.endDate);
    if (params.eventType && params.eventType !== "all") qs.set("type", params.eventType);
    if (params.personName) qs.set("person_name", params.personName);
    if (params.limit) qs.set("limit", String(params.limit));
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    return requestJson<EventListResponse>(`/events${suffix}`);
  },

  async fetchSnapshotBlobUrl(eventId: number): Promise<string> {
    const resp = await authorizedFetch(`/snapshots/${eventId}`);
    const blob = await resp.blob();
    return URL.createObjectURL(blob);
  },

  async fetchLiveSnapshotBlobUrl(): Promise<string> {
    const resp = await authorizedFetch("/cameras/live/snapshot", { cache: "no-store" });
    const blob = await resp.blob();
    return URL.createObjectURL(blob);
  },

  getPeople(): Promise<PersonItem[]> {
    return requestJson<PersonItem[]>("/people");
  },

  enrollPerson(payload: {
    name: string;
    consentDate: string;
    consentNote: string;
    files: File[];
  }): Promise<PersonEnrollResponse> {
    const form = new FormData();
    form.append("name", payload.name);
    form.append("consent_date", payload.consentDate);
    form.append("consent_note", payload.consentNote);
    for (const file of payload.files) {
      form.append("files", file);
    }
    return requestJson<PersonEnrollResponse>("/people", {
      method: "POST",
      body: form,
    });
  },

  deletePerson(personId: number): Promise<{ deleted: boolean; person_id: number }> {
    return requestJson<{ deleted: boolean; person_id: number }>(`/people/${personId}`, {
      method: "DELETE",
    });
  },

  getZones(): Promise<ZonesResponse> {
    return requestJson<ZonesResponse>("/zones");
  },

  saveZones(zones: ZonePayload[]): Promise<ZonesResponse> {
    return requestJson<ZonesResponse>("/zones", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ zones }),
    });
  },

  getNotifications(limit = 100): Promise<NotificationItem[]> {
    return requestJson<NotificationItem[]>(`/notifications?limit=${limit}`);
  },

  getSettings(): Promise<SettingsResponse> {
    return requestJson<SettingsResponse>("/settings");
  },

  updateSettings(overrides: Record<string, string>): Promise<SettingsResponse> {
    return requestJson<SettingsResponse>("/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ overrides }),
    });
  },
};

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  apiClient,
  buildWsEventsUrl,
  clearTokens,
  getAccessToken,
  getRefreshToken,
  setUnauthorizedHandler,
} from "../api/client";
import type { EventItem, User } from "../api/types";

type EventListener = (event: EventItem) => void;

interface AuthContextValue {
  user: User | null;
  isLoading: boolean;
  wsConnected: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
  subscribeEvents: (listener: EventListener) => () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({
  children,
}) => {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [wsConnected, setWsConnected] = useState(false);

  const listenersRef = useRef<Set<EventListener>>(new Set());

  const logout = useCallback(() => {
    clearTokens();
    setUser(null);
    setWsConnected(false);
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      setUser(null);
      setWsConnected(false);
    });
    return () => setUnauthorizedHandler(null);
  }, []);

  useEffect(() => {
    let cancelled = false;
    const bootstrap = async () => {
      if (!getAccessToken() && !getRefreshToken()) {
        setIsLoading(false);
        return;
      }
      try {
        const me = await apiClient.getMe();
        if (!cancelled) {
          setUser(me);
        }
      } catch {
        if (!cancelled) {
          clearTokens();
          setUser(null);
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    };
    void bootstrap();
    return () => {
      cancelled = true;
    };
  }, []);

  // Connect authenticated WebSocket for live event streaming.
  useEffect(() => {
    if (!user) return;

    let disposed = false;
    let socket: WebSocket | null = null;
    let reconnectTimer: number | null = null;
    let attempt = 0;

    const connect = () => {
      if (disposed) return;
      const token = getAccessToken();
      if (!token) return;

      socket = new WebSocket(buildWsEventsUrl(token));
      socket.onopen = () => {
        attempt = 0;
        if (!disposed) setWsConnected(true);
      };
      socket.onmessage = (msg) => {
        try {
          const parsed = JSON.parse(msg.data as string) as {
            type?: string;
            data?: EventItem;
          };
          if (parsed.type === "event" && parsed.data) {
            const eventData = parsed.data;
            listenersRef.current.forEach((cb) => cb(eventData));
          }
        } catch {
          // Ignore malformed frames
        }
      };
      socket.onclose = () => {
        if (disposed) return;
        setWsConnected(false);
        attempt += 1;
        const delay = Math.min(1000 * 2 ** (attempt - 1), 15000);
        reconnectTimer = window.setTimeout(connect, delay);
      };
      socket.onerror = () => {
        socket?.close();
      };
    };

    connect();

    return () => {
      disposed = true;
      setWsConnected(false);
      if (reconnectTimer !== null) {
        window.clearTimeout(reconnectTimer);
      }
      socket?.close();
    };
  }, [user]);

  const login = useCallback(async (username: string, password: string) => {
    await apiClient.login(username, password);
    const me = await apiClient.getMe();
    setUser(me);
  }, []);

  const subscribeEvents = useCallback((listener: EventListener) => {
    listenersRef.current.add(listener);
    return () => {
      listenersRef.current.delete(listener);
    };
  }, []);

  return (
    <AuthContext.Provider
      value={{
        user,
        isLoading,
        wsConnected,
        login,
        logout,
        subscribeEvents,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
};

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used inside AuthProvider");
  }
  return ctx;
}

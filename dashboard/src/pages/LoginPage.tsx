import React, { useState } from "react";
import { ApiError, getCustomApiUrl, setCustomApiUrl } from "../api/client";
import { useAuth } from "../context/AuthContext";
import { Button, GlassCard, Input } from "../components/ui";

export const LoginPage: React.FC = () => {
  const { login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [serverUrl, setServerUrl] = useState(() => getCustomApiUrl());
  const isCloudHost = typeof window !== "undefined" && !["localhost", "127.0.0.1"].includes(window.location.hostname);
  const [showServerConfig, setShowServerConfig] = useState(() => isCloudHost || !!getCustomApiUrl());
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!username.trim() || !password) {
      setError("Please enter your username and password.");
      return;
    }

    setCustomApiUrl(serverUrl.trim() || null);
    setSubmitting(true);
    try {
      await login(username.trim(), password);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(
          err.retryAfter
            ? `${err.message} Retry in ${err.retryAfter}s.`
            : err.message
        );
      } else {
        setError("Sign in failed. Check your credentials.");
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="login-wrapper">
      <GlassCard variant="elevated" className="login-card">
        <form onSubmit={handleSubmit} className="lg-login-form">
          <div className="lg-login-brand">
            <div className="lg-login-icon" aria-hidden="true">
              <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z" />
                <circle cx="12" cy="12" r="3" />
              </svg>
            </div>
            <h1 className="lg-login-title">VisionGuard</h1>
            <p className="lg-login-subtitle">Private Home Security</p>
          </div>

          {error && (
            <div className="lg-error-banner" role="alert">
              <span>{error}</span>
            </div>
          )}

          <div className="lg-login-fields">
            <Input
              label="Username"
              name="username"
              autoComplete="username"
              placeholder="Username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
            />

            <Input
              label="Password"
              name="password"
              type="password"
              autoComplete="current-password"
              placeholder="Password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </div>

          <Button type="submit" variant="primary" fullWidth loading={submitting}>
            Sign In
          </Button>

          <div className="lg-server-config-block">
            <button
              type="button"
              onClick={() => setShowServerConfig((prev) => !prev)}
              className="lg-server-config-toggle"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="3" />
                <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
              </svg>
              <span>{showServerConfig ? "Hide server endpoint" : "Remote server endpoint"}</span>
            </button>
            {showServerConfig && (
              <div className="lg-server-config-input">
                <Input
                  label="Endpoint URL"
                  name="serverUrl"
                  placeholder="https://...trycloudflare.com"
                  value={serverUrl}
                  onChange={(e) => {
                    setServerUrl(e.target.value);
                    setCustomApiUrl(e.target.value.trim() || null);
                  }}
                />
              </div>
            )}
          </div>
        </form>
      </GlassCard>
    </div>
  );
};

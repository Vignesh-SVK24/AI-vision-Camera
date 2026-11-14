import React, { useState } from "react";
import { ApiError, getCustomApiUrl, setCustomApiUrl } from "../api/client";
import { useAuth } from "../context/AuthContext";
import { Button, Chip, GlassCard, Input } from "../components/ui";

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
      setError("Please enter both username and password.");
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
        setError("Login failed. Check your credentials and backend server URL.");
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="login-wrapper">
      <GlassCard variant="elevated" className="login-card">
        <form onSubmit={handleSubmit} className="stack-md">
          <div className="stack-sm">
            <div className="row-wrap">
              <Chip label="Local-First" variant="accent" dot />
              <Chip label="Argon2 + JWT" variant="neutral" />
            </div>
            <h1 className="page-title">Vision Surveillance</h1>
            <p className="page-subtitle">
              Sign in with the owner account created via{" "}
              <code>tools/create_owner.py</code>.
            </p>
          </div>

          {error ? (
            <div className="error-banner" role="alert">
              {error}
            </div>
          ) : null}

          <Input
            label="Username"
            name="username"
            autoComplete="username"
            placeholder="owner"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
          />

          <Input
            label="Password"
            name="password"
            type="password"
            autoComplete="current-password"
            placeholder="Enter password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />

          <div>
            <button
              type="button"
              onClick={() => setShowServerConfig((prev) => !prev)}
              style={{
                background: "none",
                border: "none",
                color: "rgba(255, 255, 255, 0.7)",
                cursor: "pointer",
                fontSize: "0.82rem",
                padding: "0.25rem 0",
                display: "flex",
                alignItems: "center",
                gap: "0.35rem",
              }}
            >
              ⚙️ {showServerConfig ? "Hide" : "Configure"} Backend URL (for Vercel / Cloud)
            </button>
            {showServerConfig && (
              <div style={{ marginTop: "0.5rem" }}>
                <Input
                  label="Backend Server URL"
                  name="serverUrl"
                  placeholder="e.g. https://brown-lamps-strive.loca.lt"
                  value={serverUrl}
                  onChange={(e) => {
                    setServerUrl(e.target.value);
                    setCustomApiUrl(e.target.value.trim() || null);
                  }}
                />
                <p style={{ fontSize: "0.76rem", color: "rgba(255, 255, 255, 0.6)", marginTop: "0.25rem" }}>
                  Paste your active tunnel URL (e.g. https://xxx.loca.lt) when accessing from Vercel.
                </p>
              </div>
            )}
          </div>

          <Button type="submit" variant="primary" fullWidth loading={submitting}>
            Sign In
          </Button>
        </form>
      </GlassCard>
    </div>
  );
};

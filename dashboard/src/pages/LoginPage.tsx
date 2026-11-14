import React, { useState } from "react";
import { ApiError } from "../api/client";
import { useAuth } from "../context/AuthContext";
import { Button, Chip, GlassCard, Input } from "../components/ui";

export const LoginPage: React.FC = () => {
  const { login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!username.trim() || !password) {
      setError("Please enter both username and password.");
      return;
    }

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
        setError("Login failed. Please try again.");
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

          <Button type="submit" variant="primary" fullWidth loading={submitting}>
            Sign In
          </Button>
        </form>
      </GlassCard>
    </div>
  );
};

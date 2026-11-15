import React, { useCallback, useEffect, useState } from "react";
import { apiClient, ApiError } from "../api/client";
import type { PersonItem } from "../api/types";
import {
  Button,
  EmptyState,
  GlassCard,
  Input,
  Modal,
  Skeleton,
  type ToastData,
} from "../components/ui";

export interface PeoplePageProps {
  onNotify?: (toast: Omit<ToastData, "id">) => void;
}

export const PeoplePage: React.FC<PeoplePageProps> = ({ onNotify }) => {
  const [people, setPeople] = useState<PersonItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Enroll modal state
  const [modalOpen, setModalOpen] = useState(false);
  const [name, setName] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [consentChecked, setConsentChecked] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  // Delete modal state
  const [deleteTarget, setDeleteTarget] = useState<PersonItem | null>(null);
  const [deleting, setDeleting] = useState(false);

  const loadPeople = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const list = await apiClient.getPeople();
      setPeople(list || []);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load people.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadPeople();
  }, [loadPeople]);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      const selected = Array.from(e.target.files);
      if (selected.length < 3 || selected.length > 5) {
        setFormError("Please select 3 to 5 clear face photos.");
      } else {
        setFormError(null);
      }
      setFiles(selected.slice(0, 5));
    }
  };

  const handleEnrollSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);

    const trimmed = name.trim();
    if (!trimmed) {
      setFormError("Please enter a name.");
      return;
    }
    if (files.length < 3 || files.length > 5) {
      setFormError("Please choose between 3 and 5 photos.");
      return;
    }
    if (!consentChecked) {
      setFormError("Consent confirmation is required.");
      return;
    }

    setSubmitting(true);
    try {
      await apiClient.enrollPerson({
        name: trimmed,
        consentDate: new Date().toISOString().slice(0, 10),
        consentNote: "Household member gave consent for local recognition",
        files,
      });

      onNotify?.({
        title: "Person enrolled",
        description: `${trimmed} added to known faces registry.`,
        tone: "success",
      });

      setModalOpen(false);
      setName("");
      setFiles([]);
      setConsentChecked(false);
      void loadPeople();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "Failed to enroll person.");
    } finally {
      setSubmitting(false);
    }
  };

  const handleDeleteConfirm = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await apiClient.deletePerson(deleteTarget.id);
      onNotify?.({
        title: "Person removed",
        description: `${deleteTarget.name}'s biometric profile was removed.`,
        tone: "info",
      });
      setDeleteTarget(null);
      void loadPeople();
    } catch (err) {
      onNotify?.({
        title: "Deletion failed",
        description: err instanceof ApiError ? err.message : "Could not delete person.",
        tone: "danger",
      });
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="lg-people-page">
      <header className="lg-page-header">
        <div>
          <h1 className="lg-page-title">Known People</h1>
          <p className="lg-page-subtitle">
            Local face profiles for trusted household and family members
          </p>
        </div>
        <Button variant="primary" onClick={() => setModalOpen(true)}>
          Add Person
        </Button>
      </header>

      {error && (
        <div className="lg-error-banner" role="alert">
          <span>{error}</span>
          <Button size="sm" variant="secondary" onClick={() => void loadPeople()}>
            Retry
          </Button>
        </div>
      )}

      {loading ? (
        <div className="lg-avatar-grid">
          {Array.from({ length: 4 }).map((_, i) => (
            <GlassCard key={i} className="lg-person-card">
              <Skeleton variant="circle" width={72} height={72} />
              <div style={{ marginTop: 12, width: "100%" }}>
                <Skeleton variant="text" width="70%" height={20} />
              </div>
            </GlassCard>
          ))}
        </div>
      ) : people.length === 0 ? (
        <GlassCard>
          <EmptyState
            title="No people enrolled"
            description="Add family members with 3 to 5 photos to enable local recognition."
            action={
              <Button variant="primary" onClick={() => setModalOpen(true)}>
                Add First Person
              </Button>
            }
          />
        </GlassCard>
      ) : (
        <div className="lg-avatar-grid">
          {people.map((person) => {
            const initials = person.name
              .split(" ")
              .map((n) => n[0])
              .join("")
              .toUpperCase()
              .slice(0, 2);

            return (
              <GlassCard key={person.id} className="lg-person-card">
                <div className="lg-person-avatar">
                  <span className="lg-person-initials">{initials}</span>
                </div>

                <div className="lg-person-meta">
                  <strong className="lg-person-name">{person.name}</strong>
                  <span className="lg-person-sub">
                    {person.embedding_count} embeddings · {person.photo_count} photos
                  </span>
                  {person.consent_date && (
                    <span className="lg-person-consent">
                      Consent verified ({person.consent_date})
                    </span>
                  )}
                </div>

                <div className="lg-person-actions">
                  <button
                    type="button"
                    className="lg-person-del-btn"
                    onClick={() => setDeleteTarget(person)}
                    title={`Delete ${person.name}`}
                    aria-label={`Delete ${person.name}`}
                  >
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                    </svg>
                  </button>
                </div>
              </GlassCard>
            );
          })}
        </div>
      )}

      {/* Add Person Modal */}
      <Modal
        open={modalOpen}
        onClose={() => !submitting && setModalOpen(false)}
        title="Add Person for Recognition"
      >
        <form onSubmit={handleEnrollSubmit} className="lg-enroll-form">
          {formError && (
            <div className="lg-form-error" role="alert">
              {formError}
            </div>
          )}

          <Input
            label="Full Name"
            placeholder="e.g. Sarah Connor"
            value={name}
            onChange={(e) => setName(e.target.value)}
            disabled={submitting}
            required
          />

          <div className="lg-file-upload-block">
            <label className="lg-input-label" htmlFor="photo-upload">
              Upload 3 to 5 Face Photos (clear angles)
            </label>
            <input
              id="photo-upload"
              type="file"
              accept="image/*"
              multiple
              onChange={handleFileChange}
              disabled={submitting}
              className="lg-file-input"
            />
            <span className="lg-file-hint">
              {files.length > 0
                ? `${files.length} photos selected`
                : "Choose 3-5 images showing clear frontal and slight angled views."}
            </span>
          </div>

          <label className="lg-consent-row">
            <input
              type="checkbox"
              checked={consentChecked}
              onChange={(e) => setConsentChecked(e.target.checked)}
              disabled={submitting}
              required
            />
            <span className="lg-consent-text">
              I confirm that this person has given explicit consent for their face photos to be processed and stored locally for household recognition.
            </span>
          </label>

          <div className="lg-modal-btn-row">
            <Button
              type="button"
              variant="secondary"
              onClick={() => setModalOpen(false)}
              disabled={submitting}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              variant="primary"
              loading={submitting}
              disabled={files.length < 3 || files.length > 5 || !consentChecked}
            >
              Enroll Person
            </Button>
          </div>
        </form>
      </Modal>

      {/* Delete Confirmation Modal */}
      <Modal
        open={Boolean(deleteTarget)}
        onClose={() => !deleting && setDeleteTarget(null)}
        title="Confirm Removal"
      >
        {deleteTarget && (
          <div className="lg-delete-modal-body">
            <p>
              Are you sure you want to remove <strong>{deleteTarget.name}</strong>?
            </p>
            <p className="lg-delete-warning">
              All stored biometric embeddings and reference photos will be permanently deleted from the local database.
            </p>
            <div className="lg-modal-btn-row">
              <Button
                variant="secondary"
                onClick={() => setDeleteTarget(null)}
                disabled={deleting}
              >
                Cancel
              </Button>
              <Button
                variant="secondary"
                className="lg-btn--danger"
                loading={deleting}
                onClick={handleDeleteConfirm}
              >
                Delete Profile
              </Button>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
};

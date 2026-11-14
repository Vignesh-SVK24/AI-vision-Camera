import React, { useCallback, useEffect, useState } from "react";
import { apiClient, ApiError } from "../api/client";
import type { PersonEnrollResponse, PersonItem } from "../api/types";
import {
  Button,
  Chip,
  EmptyState,
  GlassCard,
  Input,
  Modal,
  Skeleton,
  type ToastData,
} from "../components/ui";

export interface PeoplePageProps {
  onNotify: (toast: Omit<ToastData, "id">) => void;
}

function todayIsoDate(): string {
  return new Date().toISOString().slice(0, 10);
}

export const PeoplePage: React.FC<PeoplePageProps> = ({ onNotify }) => {
  const [people, setPeople] = useState<PersonItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [enrollOpen, setEnrollOpen] = useState(false);
  const [name, setName] = useState("");
  const [consentDate, setConsentDate] = useState(todayIsoDate);
  const [consentNote, setConsentNote] = useState("");
  const [consentChecked, setConsentChecked] = useState(false);
  const [files, setFiles] = useState<File[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<PersonEnrollResponse | null>(null);

  const [deleteTarget, setDeleteTarget] = useState<PersonItem | null>(null);
  const [deleting, setDeleting] = useState(false);

  const loadPeople = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const list = await apiClient.getPeople();
      setPeople(list);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load enrolled people.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadPeople();
  }, [loadPeople]);

  const resetForm = () => {
    setName("");
    setConsentDate(todayIsoDate());
    setConsentNote("");
    setConsentChecked(false);
    setFiles([]);
    setFormError(null);
  };

  const handleEnrollSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);

    if (!name.trim()) {
      setFormError("Please enter the person's name.");
      return;
    }
    if (!consentChecked) {
      setFormError("Explicit consent confirmation is required before enrolling face data.");
      return;
    }
    if (!consentNote.trim()) {
      setFormError("Please enter a brief consent note (e.g., 'Verbal consent recorded').");
      return;
    }
    if (files.length === 0) {
      setFormError("Please select at least one clear front-facing photo (JPG, PNG, or WebP).");
      return;
    }

    setSubmitting(true);
    try {
      const res = await apiClient.enrollPerson({
        name: name.trim(),
        consentDate,
        consentNote: consentNote.trim(),
        files,
      });
      setLastResult(res);
      setEnrollOpen(false);
      resetForm();
      onNotify({
        title: `Enrolled ${res.name}`,
        description: `${res.enrolled_embeddings} face embedding(s) saved.`,
        tone: "success",
      });
      await loadPeople();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "Enrollment failed.");
    } finally {
      setSubmitting(false);
    }
  };

  const handleDeleteConfirm = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await apiClient.deletePerson(deleteTarget.id);
      onNotify({
        title: "Person removed",
        description: `${deleteTarget.name} and their face embeddings were deleted.`,
        tone: "info",
      });
      setDeleteTarget(null);
      await loadPeople();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to delete person.");
      setDeleteTarget(null);
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="page-container">
      <header className="page-header">
        <div>
          <h1 className="page-title">People</h1>
          <p className="page-subtitle">
            Manage enrolled household members. Unrecognized faces stay labeled Unknown.
          </p>
        </div>
        <div className="row-wrap">
          <Button variant="secondary" onClick={() => void loadPeople()} disabled={loading}>
            Refresh
          </Button>
          <Button
            variant="primary"
            onClick={() => {
              resetForm();
              setLastResult(null);
              setEnrollOpen(true);
            }}
          >
            Enroll Person
          </Button>
        </div>
      </header>

      {error ? (
        <div className="error-banner row-wrap" role="alert">
          <span>{error}</span>
          <Button size="sm" variant="secondary" onClick={() => void loadPeople()}>
            Retry
          </Button>
        </div>
      ) : null}

      {lastResult && Object.keys(lastResult.rejected_reasons).length > 0 ? (
        <GlassCard>
          <div className="stack-sm">
            <div className="row-wrap">
              <strong>Partial Photo Warnings for {lastResult.name}</strong>
              <Chip label={`${lastResult.enrolled_embeddings} accepted`} variant="success" />
            </div>
            {Object.entries(lastResult.rejected_reasons).map(([fileName, reason]) => (
              <p key={fileName} className="page-subtitle">
                <strong>{fileName}:</strong> {reason}
              </p>
            ))}
          </div>
        </GlassCard>
      ) : null}

      {loading ? (
        <div className="grid-cards grid-cards--2">
          <GlassCard>
            <Skeleton variant="card" height={130} />
          </GlassCard>
          <GlassCard>
            <Skeleton variant="card" height={130} />
          </GlassCard>
        </div>
      ) : people.length === 0 ? (
        <GlassCard>
          <EmptyState
            title="No enrolled people yet"
            description="Enroll trusted family members with their explicit consent so alerts distinguish known faces from strangers."
            action={
              <Button
                variant="primary"
                onClick={() => {
                  resetForm();
                  setEnrollOpen(true);
                }}
              >
                Enroll First Person
              </Button>
            }
          />
        </GlassCard>
      ) : (
        <div className="grid-cards grid-cards--2">
          {people.map((person) => (
            <GlassCard key={person.id}>
              <div className="stack-md">
                <div className="page-header">
                  <div>
                    <h2 style={{ margin: 0, fontSize: "1.1rem" }}>{person.name}</h2>
                    <p className="page-subtitle">
                      Added {new Date(person.created_at).toLocaleDateString()}
                    </p>
                  </div>
                  <Chip
                    label={person.is_trusted ? "Trusted" : "Standard"}
                    variant={person.is_trusted ? "success" : "neutral"}
                    dot
                  />
                </div>

                <div className="row-wrap">
                  <Chip label={`${person.embedding_count} embeddings`} variant="accent" />
                  <Chip label={`${person.photo_count} photos`} variant="neutral" />
                  {person.consent_date ? (
                    <Chip label={`Consent: ${person.consent_date}`} variant="neutral" />
                  ) : null}
                </div>

                {person.consent_note ? (
                  <p className="page-subtitle">Consent note: {person.consent_note}</p>
                ) : null}

                <div className="row-wrap" style={{ justifyContent: "flex-end" }}>
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => setDeleteTarget(person)}
                  >
                    Delete
                  </Button>
                </div>
              </div>
            </GlassCard>
          ))}
        </div>
      )}

      <Modal
        open={enrollOpen}
        title="Enroll Person"
        onClose={() => {
          if (!submitting) setEnrollOpen(false);
        }}
        footer={
          <>
            <Button
              variant="secondary"
              onClick={() => setEnrollOpen(false)}
              disabled={submitting}
            >
              Cancel
            </Button>
            <Button
              variant="primary"
              type="submit"
              form="enroll-person-form"
              loading={submitting}
            >
              Enroll
            </Button>
          </>
        }
      >
        <form id="enroll-person-form" className="stack-md" onSubmit={handleEnrollSubmit}>
          {formError ? (
            <div className="error-banner" role="alert">
              {formError}
            </div>
          ) : null}

          <Input
            label="Full Name"
            placeholder="e.g., Alex"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />

          <Input
            label="Consent Date"
            type="date"
            value={consentDate}
            onChange={(e) => setConsentDate(e.target.value)}
            required
          />

          <Input
            label="Consent Note"
            placeholder="e.g., Household member gave consent for local recognition"
            value={consentNote}
            onChange={(e) => setConsentNote(e.target.value)}
            required
          />

          <div className="stack-sm">
            <label htmlFor="enroll-photos" className="lg-field__label">
              Photos (1–10 clear face images, max 10 MB each)
            </label>
            <input
              id="enroll-photos"
              type="file"
              accept="image/jpeg,image/png,image/webp"
              multiple
              className="file-input-control"
              onChange={(e) => {
                const selected = e.target.files ? Array.from(e.target.files) : [];
                setFiles(selected);
              }}
            />
            {files.length > 0 ? (
              <span className="page-subtitle">{files.length} file(s) selected</span>
            ) : null}
          </div>

          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={consentChecked}
              onChange={(e) => setConsentChecked(e.target.checked)}
              required
            />
            <span>
              I confirm this person has provided informed consent to store their face
              embeddings locally for home surveillance recognition.
            </span>
          </label>
        </form>
      </Modal>

      <Modal
        open={Boolean(deleteTarget)}
        title="Delete Enrolled Person"
        onClose={() => {
          if (!deleting) setDeleteTarget(null);
        }}
        footer={
          <>
            <Button
              variant="secondary"
              onClick={() => setDeleteTarget(null)}
              disabled={deleting}
            >
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={() => void handleDeleteConfirm()}
              loading={deleting}
            >
              Confirm Delete
            </Button>
          </>
        }
      >
        <p style={{ margin: 0 }}>
          Remove <strong>{deleteTarget?.name}</strong> and all associated face embeddings?
          Future detections of this person will be labeled <strong>Unknown</strong>.
        </p>
      </Modal>
    </div>
  );
};

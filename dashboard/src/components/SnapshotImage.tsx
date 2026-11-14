import React, { useEffect, useState } from "react";
import { apiClient } from "../api/client";
import { Skeleton } from "./ui";

export interface SnapshotImageProps {
  eventId: number;
  alt: string;
  className?: string;
  onClick?: () => void;
}

export const SnapshotImage: React.FC<SnapshotImageProps> = ({
  eventId,
  alt,
  className = "",
  onClick,
}) => {
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let active = true;
    let createdUrl: string | null = null;
    setFailed(false);
    setBlobUrl(null);

    apiClient
      .fetchSnapshotBlobUrl(eventId)
      .then((url) => {
        if (!active) {
          URL.revokeObjectURL(url);
          return;
        }
        createdUrl = url;
        setBlobUrl(url);
      })
      .catch(() => {
        if (active) setFailed(true);
      });

    return () => {
      active = false;
      if (createdUrl) {
        URL.revokeObjectURL(createdUrl);
      }
    };
  }, [eventId]);

  if (failed) {
    return (
      <div className={`snapshot-fallback ${className}`}>
        Snapshot unavailable
      </div>
    );
  }

  if (!blobUrl) {
    return <Skeleton variant="card" height={120} className={className} />;
  }

  return (
    <img
      src={blobUrl}
      alt={alt}
      className={className}
      onClick={onClick}
      style={onClick ? { cursor: "pointer" } : undefined}
    />
  );
};

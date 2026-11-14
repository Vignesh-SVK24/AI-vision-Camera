import React from "react";

export type ChipVariant = "neutral" | "accent" | "success" | "warning" | "danger";

export interface ChipProps {
  label: string;
  variant?: ChipVariant;
  dot?: boolean;
  selected?: boolean;
  onClick?: () => void;
  className?: string;
}

export const Chip: React.FC<ChipProps> = ({
  label,
  variant = "neutral",
  dot = false,
  selected = false,
  onClick,
  className = "",
}) => {
  const classes = [
    "lg-chip",
    variant !== "neutral" ? `lg-chip--${variant}` : "",
    selected ? "lg-chip--selected" : "",
    onClick ? "lg-chip--interactive" : "",
    className,
  ]
    .filter(Boolean)
    .join(" ");

  if (onClick) {
    return (
      <button
        type="button"
        className={classes}
        onClick={onClick}
        aria-pressed={selected}
      >
        {dot && <span className="lg-chip__dot" aria-hidden="true" />}
        <span>{label}</span>
      </button>
    );
  }

  return (
    <span className={classes}>
      {dot && <span className="lg-chip__dot" aria-hidden="true" />}
      <span>{label}</span>
    </span>
  );
};

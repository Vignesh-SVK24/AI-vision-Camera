import React from "react";

export interface SkeletonProps {
  variant?: "text" | "card" | "circle";
  width?: string | number;
  height?: string | number;
  className?: string;
}

export const Skeleton: React.FC<SkeletonProps> = ({
  variant = "text",
  width = "100%",
  height = variant === "text" ? 16 : variant === "circle" ? 44 : 112,
  className = "",
}) => {
  const classes = [
    "lg-skeleton",
    variant === "circle" ? "lg-skeleton--circle" : "",
    variant === "card" ? "lg-skeleton--card" : "",
    className,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <span
      className={classes}
      style={{ width, height }}
      aria-hidden="true"
    />
  );
};

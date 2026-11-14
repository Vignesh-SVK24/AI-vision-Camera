import React from "react";

export interface GlassCardProps extends React.HTMLAttributes<HTMLDivElement> {
  variant?: "default" | "elevated";
  compact?: boolean;
  hoverGlow?: boolean;
  animateEntry?: boolean;
}

export const GlassCard: React.FC<GlassCardProps> = ({
  variant = "default",
  compact = false,
  hoverGlow = true,
  animateEntry = true,
  className = "",
  children,
  ...rest
}) => {
  const classes = [
    "lg-card",
    variant === "elevated" ? "lg-card--elevated" : "",
    compact ? "lg-card--compact" : "",
    hoverGlow ? "lg-card--glow" : "",
    animateEntry ? "lg-card--animate" : "",
    className,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={classes} {...rest}>
      {children}
    </div>
  );
};

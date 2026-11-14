import React, { useId } from "react";

export interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  hint?: string;
  error?: string;
  leadingIcon?: React.ReactNode;
}

export const Input: React.FC<InputProps> = ({
  label,
  hint,
  error,
  leadingIcon,
  id,
  className = "",
  ...rest
}) => {
  const autoId = useId();
  const inputId = id ?? autoId;
  const descId = error ? `${inputId}-error` : hint ? `${inputId}-hint` : undefined;

  return (
    <div className={`lg-field ${className}`}>
      {label ? (
        <label className="lg-field__label" htmlFor={inputId}>
          {label}
        </label>
      ) : null}
      <div className={`lg-input-wrap ${error ? "lg-input-wrap--error" : ""}`}>
        {leadingIcon ? <span aria-hidden="true">{leadingIcon}</span> : null}
        <input
          id={inputId}
          className="lg-input"
          aria-invalid={Boolean(error) || undefined}
          aria-describedby={descId}
          {...rest}
        />
      </div>
      {error ? (
        <p id={`${inputId}-error`} className="lg-field__error">
          {error}
        </p>
      ) : hint ? (
        <p id={`${inputId}-hint`} className="lg-field__hint">
          {hint}
        </p>
      ) : null}
    </div>
  );
};

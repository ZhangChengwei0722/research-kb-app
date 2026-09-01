import { useState, type ReactNode } from "react";

type ConfirmButtonProps = {
  onConfirm: () => void;
  /** Accessible name for the trigger, e.g. "停止服务". */
  title: string;
  /** Consequence shown once armed, e.g. what the user will have to redo. */
  description: string;
  /** Label of the destructive confirmation control. */
  confirmLabel: string;
  className?: string;
  disabled?: boolean;
  children: ReactNode;
};

/**
 * Two-step confirmation for destructive actions.
 *
 * The product previously had zero confirmation affordances, so a single stray
 * click on the header power icon terminated the backend process.
 */
export function ConfirmButton({
  onConfirm,
  title,
  description,
  confirmLabel,
  className,
  disabled,
  children,
}: ConfirmButtonProps) {
  const [armed, setArmed] = useState(false);

  if (!armed) {
    return (
      <button
        type="button"
        className={className}
        title={title}
        aria-label={title}
        disabled={disabled}
        onClick={() => setArmed(true)}
      >
        {children}
      </button>
    );
  }

  return (
    <span className="confirm-inline" role="alertdialog" aria-label={title}>
      <span className="confirm-inline-text">{description}</span>
      <button
        type="button"
        className="danger-button"
        autoFocus
        onClick={() => {
          setArmed(false);
          onConfirm();
        }}
      >
        {confirmLabel}
      </button>
      <button type="button" className="secondary-button" onClick={() => setArmed(false)}>
        取消
      </button>
    </span>
  );
}

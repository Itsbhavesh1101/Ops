import React from "react";
import { Inbox } from "lucide-react";
import { Button } from "./Button";

export interface EmptyStateProps {
  icon?: React.ReactNode;
  title: string;
  description: string;
  actionLabel?: string;
  actionAriaLabel?: string;
  onAction?: () => void;
  className?: string;
}

export const EmptyState: React.FC<EmptyStateProps> = ({
  icon,
  title,
  description,
  actionLabel,
  actionAriaLabel,
  onAction,
  className = "",
}) => {
  return (
    <div className={`p-10 border border-dashed border-slate-200/80 rounded-xl text-center flex flex-col items-center justify-center bg-white ${className}`}>
      <div className="w-10 h-10 rounded-full bg-slate-100 flex items-center justify-center text-slate-400 mb-3">
        {icon || <Inbox className="w-5 h-5" />}
      </div>
      <h3 className="text-sm font-semibold text-slate-900 mb-1">{title}</h3>
      <p className="text-xs text-slate-500 max-w-sm mb-4">{description}</p>
      {actionLabel && onAction && (
        <Button
          variant="primary"
          size="sm"
          onClick={onAction}
          aria-label={actionAriaLabel || actionLabel}
        >
          <span>{actionLabel}</span>
          {actionAriaLabel && <span className="sr-only">{actionAriaLabel}</span>}
        </Button>
      )}
    </div>
  );
};

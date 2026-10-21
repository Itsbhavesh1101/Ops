import React, { useState } from "react";
import { AlertCircle, ChevronDown, ChevronUp, RefreshCw } from "lucide-react";
import { Button } from "./Button";

export interface ErrorStateProps {
  title?: string;
  message: string;
  technicalDetails?: string;
  onRetry?: () => void;
  className?: string;
}

export const ErrorState: React.FC<ErrorStateProps> = ({
  title = "Failed to load operational data",
  message,
  technicalDetails,
  onRetry,
  className = "",
}) => {
  const [showDetails, setShowDetails] = useState(false);

  return (
    <div className={`p-4 bg-rose-50 border border-rose-200 rounded-lg text-left space-y-3 ${className}`}>
      <div className="flex items-start gap-3">
        <AlertCircle className="w-4 h-4 text-rose-600 shrink-0 mt-0.5" />
        <div className="flex-1 min-w-0">
          <h4 className="text-sm font-semibold text-rose-900">{title}</h4>
          <p className="text-xs text-rose-700 mt-0.5">{message}</p>
        </div>
        {onRetry && (
          <Button
            variant="secondary"
            size="sm"
            onClick={onRetry}
            leftIcon={<RefreshCw className="w-3.5 h-3.5" />}
            className="border-rose-300 text-rose-700 hover:bg-rose-100 bg-white"
          >
            Retry
          </Button>
        )}
      </div>

      {technicalDetails && (
        <div className="pt-2 border-t border-rose-200/60">
          <button
            onClick={() => setShowDetails(!showDetails)}
            className="flex items-center gap-1 text-[11px] font-mono text-rose-600 hover:text-rose-800 cursor-pointer bg-transparent border-none p-0"
          >
            <span>Diagnostics</span>
            {showDetails ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
          </button>
          {showDetails && (
            <pre className="mt-2 p-2.5 bg-white border border-rose-200 rounded text-[11px] text-rose-800 font-mono overflow-x-auto whitespace-pre-wrap">
              {technicalDetails}
            </pre>
          )}
        </div>
      )}
    </div>
  );
};

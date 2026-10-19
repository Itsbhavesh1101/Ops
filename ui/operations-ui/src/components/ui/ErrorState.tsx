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
    <div className={`p-4 bg-rose-950/40 border border-rose-900/60 rounded-xl text-left space-y-3 ${className}`}>
      <div className="flex items-start gap-3">
        <AlertCircle className="w-5 h-5 text-rose-400 shrink-0 mt-0.5" />
        <div className="flex-1 min-w-0">
          <h4 className="text-sm font-semibold text-rose-200">{title}</h4>
          <p className="text-xs text-rose-300 mt-1">{message}</p>
        </div>
        {onRetry && (
          <Button
            variant="outline"
            size="sm"
            onClick={onRetry}
            leftIcon={<RefreshCw className="w-3.5 h-3.5" />}
            className="border-rose-800 text-rose-200 hover:bg-rose-900/40"
          >
            Retry
          </Button>
        )}
      </div>

      {technicalDetails && (
        <div className="pt-2 border-t border-rose-900/40">
          <button
            onClick={() => setShowDetails(!showDetails)}
            className="flex items-center gap-1 text-[11px] font-mono text-rose-400 hover:text-rose-300 cursor-pointer bg-transparent border-none p-0"
          >
            <span>Diagnostics</span>
            {showDetails ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
          </button>
          {showDetails && (
            <pre className="mt-2 p-2 bg-slate-950/80 border border-rose-950 rounded text-[10px] text-rose-300 font-mono overflow-x-auto whitespace-pre-wrap">
              {technicalDetails}
            </pre>
          )}
        </div>
      )}
    </div>
  );
};

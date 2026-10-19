import React from "react";
import { Loader2 } from "lucide-react";

export interface LoadingStateProps {
  message?: string;
  className?: string;
}

export const LoadingState: React.FC<LoadingStateProps> = ({
  message = "Loading data from system of record...",
  className = "",
}) => {
  return (
    <div className={`p-12 text-center flex flex-col items-center justify-center text-slate-400 text-xs gap-3 ${className}`}>
      <Loader2 className="w-6 h-6 animate-spin text-indigo-400" />
      <span>{message}</span>
    </div>
  );
};

export const Skeleton: React.FC<{ className?: string }> = ({ className = "" }) => (
  <div className={`animate-pulse bg-slate-800/80 rounded ${className}`} />
);

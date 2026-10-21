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
    <div className={`p-12 text-center flex flex-col items-center justify-center text-slate-500 text-xs gap-3 ${className}`}>
      <Loader2 className="w-5 h-5 animate-spin text-indigo-600" />
      <span>{message}</span>
    </div>
  );
};

export const Skeleton: React.FC<{ className?: string }> = ({ className = "" }) => (
  <div className={`animate-pulse bg-slate-200 rounded ${className}`} />
);

export const SkeletonRow: React.FC<{ cols?: number }> = ({ cols = 5 }) => (
  <tr className="border-b border-slate-100">
    {Array.from({ length: cols }).map((_, i) => (
      <td key={i} className="px-4 py-3">
        <Skeleton className={`h-4 ${i === 0 ? "w-32" : "w-20"}`} />
      </td>
    ))}
  </tr>
);

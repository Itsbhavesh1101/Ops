import React from "react";
import { Badge, BadgeVariant } from "./Badge";

export interface MetricCardProps {
  title: string;
  value: string | number;
  subtitle?: string;
  icon?: React.ReactNode;
  badgeText?: string;
  badgeVariant?: BadgeVariant;
  trendText?: string;
  trendType?: "positive" | "negative" | "neutral";
  className?: string;
}

export const MetricCard: React.FC<MetricCardProps> = ({
  title,
  value,
  subtitle,
  icon,
  badgeText,
  badgeVariant = "info",
  trendText,
  trendType = "neutral",
  className = "",
}) => {
  const trendColor = {
    positive: "text-emerald-700",
    negative: "text-rose-700",
    neutral: "text-slate-500",
  }[trendType];

  return (
    <div className={`bg-white border border-slate-200 rounded-lg p-4 shadow-xs flex flex-col justify-between ${className}`}>
      <div>
        <div className="flex items-center justify-between gap-1.5 text-xs text-slate-500 mb-1.5 min-w-0">
          <span className="flex items-center gap-1.5 font-medium uppercase tracking-wider text-[11px] truncate">
            {icon && <span className="text-indigo-600 shrink-0">{icon}</span>}
            <span className="truncate">{title}</span>
          </span>
          {badgeText && (
            <Badge variant={badgeVariant} size="sm" className="shrink-0">
              {badgeText}
            </Badge>
          )}
        </div>
        <div className="text-xl md:text-2xl font-bold text-slate-900 tracking-tight tabular-nums my-0.5">
          {value}
        </div>
      </div>
      {(subtitle || trendText) && (
        <div className="mt-3 pt-2.5 border-t border-slate-100 flex items-center justify-between text-xs gap-2 min-w-0">
          {subtitle && <span className="text-slate-500 truncate text-[11px]">{subtitle}</span>}
          {trendText && <span className={`font-medium shrink-0 text-[11px] ${trendColor}`}>{trendText}</span>}
        </div>
      )}
    </div>
  );
};

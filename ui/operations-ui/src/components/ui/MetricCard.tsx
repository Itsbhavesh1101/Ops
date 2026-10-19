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
    positive: "text-emerald-400",
    negative: "text-rose-400",
    neutral: "text-slate-400",
  }[trendType];

  return (
    <div className={`bg-slate-900 border border-slate-800 rounded-xl p-4 shadow flex flex-col justify-between ${className}`}>
      <div>
        <div className="flex items-center justify-between text-xs text-slate-400 mb-2">
          <span className="flex items-center gap-1.5 font-semibold uppercase tracking-wider">
            {icon && <span className="text-indigo-400">{icon}</span>}
            {title}
          </span>
          {badgeText && (
            <Badge variant={badgeVariant} size="sm">
              {badgeText}
            </Badge>
          )}
        </div>
        <div className="text-2xl font-bold text-white tracking-tight my-1">
          {value}
        </div>
      </div>
      {(subtitle || trendText) && (
        <div className="mt-2 pt-2 border-t border-slate-800/80 flex items-center justify-between text-xs">
          {subtitle && <span className="text-slate-400 truncate">{subtitle}</span>}
          {trendText && <span className={`font-medium ${trendColor}`}>{trendText}</span>}
        </div>
      )}
    </div>
  );
};

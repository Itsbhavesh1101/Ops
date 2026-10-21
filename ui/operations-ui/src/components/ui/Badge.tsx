import React from "react";

export type BadgeVariant = "success" | "warning" | "error" | "info" | "neutral" | "primary";

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  variant?: BadgeVariant;
  size?: "sm" | "md";
  dot?: boolean;
}

export const formatStatusText = (text: string): string => {
  if (text.includes("_")) {
    return text
      .split("_")
      .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
      .join(" ");
  }
  return text;
};

export const Badge: React.FC<BadgeProps> = ({
  children,
  variant = "neutral",
  size = "md",
  dot = false,
  className = "",
  ...props
}) => {
  const sizeStyles = {
    sm: "px-2 py-0.5 text-[11px]",
    md: "px-2.5 py-0.5 text-xs",
  }[size];

  const variantStyles: Record<BadgeVariant, { container: string; dot: string }> = {
    success: {
      container: "bg-emerald-50 text-emerald-700 border-emerald-200/80",
      dot: "bg-emerald-500",
    },
    warning: {
      container: "bg-amber-50 text-amber-800 border-amber-200/80",
      dot: "bg-amber-500",
    },
    error: {
      container: "bg-rose-50 text-rose-700 border-rose-200/80",
      dot: "bg-rose-500",
    },
    info: {
      container: "bg-blue-50 text-blue-700 border-blue-200/80",
      dot: "bg-blue-500",
    },
    primary: {
      container: "bg-indigo-50 text-indigo-700 border-indigo-200/80",
      dot: "bg-indigo-500",
    },
    neutral: {
      container: "bg-slate-100 text-slate-700 border-slate-200/80",
      dot: "bg-slate-400",
    },
  };

  const currentVariant = variantStyles[variant];
  const renderedContent = typeof children === "string" ? formatStatusText(children) : children;

  return (
    <span
      className={`inline-flex items-center gap-1.5 font-medium rounded-full border whitespace-nowrap shrink-0 ${sizeStyles} ${currentVariant.container} ${className}`}
      {...props}
    >
      {dot && <span className={`w-1.5 h-1.5 rounded-full ${currentVariant.dot} shrink-0`} />}
      <span>{renderedContent}</span>
    </span>
  );
};

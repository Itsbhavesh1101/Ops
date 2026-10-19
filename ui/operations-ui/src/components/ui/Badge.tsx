import React from "react";

export type BadgeVariant = "success" | "warning" | "error" | "info" | "neutral" | "primary";

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  variant?: BadgeVariant;
  size?: "sm" | "md";
  dot?: boolean;
}

export const Badge: React.FC<BadgeProps> = ({
  children,
  variant = "neutral",
  size = "md",
  dot = false,
  className = "",
  ...props
}) => {
  const sizeStyles = {
    sm: "px-2 py-0.5 text-[10px]",
    md: "px-2.5 py-1 text-xs",
  }[size];

  const variantStyles: Record<BadgeVariant, { container: string; dot: string }> = {
    success: {
      container: "bg-emerald-950/60 text-emerald-300 border-emerald-800/80",
      dot: "bg-emerald-400",
    },
    warning: {
      container: "bg-amber-950/60 text-amber-300 border-amber-800/80",
      dot: "bg-amber-400",
    },
    error: {
      container: "bg-rose-950/60 text-rose-300 border-rose-800/80",
      dot: "bg-rose-400",
    },
    info: {
      container: "bg-blue-950/60 text-blue-300 border-blue-800/80",
      dot: "bg-blue-400",
    },
    primary: {
      container: "bg-indigo-950/60 text-indigo-300 border-indigo-800/80",
      dot: "bg-indigo-400",
    },
    neutral: {
      container: "bg-slate-800/80 text-slate-300 border-slate-700",
      dot: "bg-slate-400",
    },
  };

  const currentVariant = variantStyles[variant];

  return (
    <span
      className={`inline-flex items-center gap-1.5 font-semibold rounded-md border tracking-wider uppercase ${sizeStyles} ${currentVariant.container} ${className}`}
      {...props}
    >
      {dot && <span className={`w-1.5 h-1.5 rounded-full ${currentVariant.dot}`} />}
      {children}
    </span>
  );
};

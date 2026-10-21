import React from "react";
import { Loader2 } from "lucide-react";

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "outline" | "ghost" | "danger";
  size?: "sm" | "md" | "lg";
  isLoading?: boolean;
  leftIcon?: React.ReactNode;
  rightIcon?: React.ReactNode;
}

export const Button: React.FC<ButtonProps> = ({
  children,
  variant = "primary",
  size = "md",
  isLoading = false,
  leftIcon,
  rightIcon,
  disabled,
  className = "",
  ...props
}) => {
  const baseStyles =
    "inline-flex items-center justify-center font-medium rounded-lg transition-colors cursor-pointer select-none disabled:opacity-50 disabled:cursor-not-allowed border text-sm";

  const sizeStyles = {
    sm: "px-2.5 py-1 text-xs gap-1.5 h-8",
    md: "px-3.5 py-1.5 text-sm gap-2 h-9",
    lg: "px-4 py-2 text-sm gap-2 h-10",
  }[size];

  const variantStyles = {
    primary: "bg-indigo-600 hover:bg-indigo-700 text-white border-indigo-600 shadow-xs font-semibold",
    secondary: "bg-white hover:bg-slate-50 text-slate-800 border-slate-300 shadow-xs",
    outline: "bg-white hover:bg-slate-50 text-slate-700 border-slate-300 shadow-xs",
    ghost: "bg-transparent hover:bg-slate-100 text-slate-600 border-transparent",
    danger: "bg-rose-600 hover:bg-rose-700 text-white border-rose-600 shadow-xs font-semibold",
  }[variant];

  return (
    <button
      disabled={disabled || isLoading}
      className={`${baseStyles} ${sizeStyles} ${variantStyles} ${className}`}
      {...props}
    >
      {isLoading ? (
        <Loader2 className="w-3.5 h-3.5 animate-spin text-current shrink-0" />
      ) : (
        leftIcon && <span className="shrink-0">{leftIcon}</span>
      )}
      {children}
      {!isLoading && rightIcon && <span className="shrink-0">{rightIcon}</span>}
    </button>
  );
};

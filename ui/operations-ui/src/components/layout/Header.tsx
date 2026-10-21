import React from "react";
import { Menu, Building2 } from "lucide-react";
import { SystemStatusIndicator } from "./SystemStatusIndicator";
import { UserMenu } from "./UserMenu";
import { useAuth } from "../../context/AuthContext";

interface HeaderProps {
  onOpenSidebar: () => void;
}

export const Header: React.FC<HeaderProps> = ({ onOpenSidebar }) => {
  const { user } = useAuth();

  const getFriendlyWorkspaceName = (tenantId?: string) => {
    if (!tenantId) return "Demo Workspace";
    if (tenantId === "tenant-demo-real-001") return "IR School 2";
    if (tenantId === "tenant-pilot-001") return "Pilot School";
    if (tenantId === "tenant-engineering") return "Engineering Workspace";
    return "Demo Workspace";
  };

  return (
    <header className="h-14 px-4 md:px-6 bg-white border-b border-slate-200/80 flex items-center justify-between gap-3 sticky top-0 z-30 shadow-xs">
      {/* Left: Mobile Toggle & Tenant Workspace Context */}
      <div className="flex items-center gap-3">
        <button
          onClick={onOpenSidebar}
          aria-label="Open navigation menu"
          className="md:hidden text-slate-500 hover:text-slate-800 p-1.5 rounded-md bg-slate-100 hover:bg-slate-200 border-none cursor-pointer transition-colors"
        >
          <Menu className="w-4 h-4" />
        </button>

        {/* Tenant/Workspace Indicator Badge */}
        {user?.tenant_id && (
          <div
            className="flex items-center gap-2 px-2.5 py-1 rounded-md bg-slate-50/80 border border-slate-200/80 text-xs"
            title={`Tenant ID: ${user.tenant_id}`}
          >
            <Building2 className="w-3.5 h-3.5 text-indigo-600 shrink-0" />
            <span className="font-semibold text-slate-800 tracking-tight">
              {getFriendlyWorkspaceName(user.tenant_id)}
            </span>
            <span className="text-[10px] text-slate-400 font-mono hidden sm:inline border-l border-slate-200 pl-1.5">
              {user.tenant_id}
            </span>
          </div>
        )}
      </div>

      {/* Right: Health Probe & User Account Menu */}
      <div className="flex items-center gap-3">
        <SystemStatusIndicator />
        <div className="h-4 w-px bg-slate-200 hidden sm:block" />
        <UserMenu />
      </div>
    </header>
  );
};

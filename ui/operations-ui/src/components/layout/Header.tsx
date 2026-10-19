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

  return (
    <header className="h-16 px-4 md:px-6 bg-slate-900 border-b border-slate-800 flex items-center justify-between gap-3 sticky top-0 z-30">
      {/* Left: Mobile Toggle & Context */}
      <div className="flex items-center gap-3">
        <button
          onClick={onOpenSidebar}
          aria-label="Open navigation menu"
          className="md:hidden text-slate-400 hover:text-white p-2 rounded-lg bg-slate-800 border-none cursor-pointer"
        >
          <Menu className="w-5 h-5" />
        </button>

        {/* Tenant Indicator Badge */}
        {user?.tenant_id && (
          <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-slate-800/80 border border-slate-700/80 text-xs">
            <Building2 className="w-3.5 h-3.5 text-indigo-400 shrink-0" />
            <span className="text-slate-400 font-medium hidden sm:inline">Tenant:</span>
            <strong className="text-white font-mono">{user.tenant_id}</strong>
          </div>
        )}
      </div>

      {/* Right: Health & Account Menu */}
      <div className="flex items-center gap-3">
        <SystemStatusIndicator />
        <div className="h-5 w-px bg-slate-800 hidden sm:block" />
        <UserMenu />
      </div>
    </header>
  );
};

import React, { useState } from "react";
import { useAuth } from "../../context/AuthContext";
import { LogOut, Shield, Building2, Key } from "lucide-react";

export const UserMenu: React.FC = () => {
  const { user, logout } = useAuth();
  const [isOpen, setIsOpen] = useState(false);

  if (!user) return null;

  const initials = user.display_name
    ? user.display_name
        .split(" ")
        .map((p) => p[0])
        .join("")
        .substring(0, 2)
        .toUpperCase()
    : user.principal_id.substring(0, 2).toUpperCase();

  return (
    <div className="relative">
      <button
        onClick={() => setIsOpen(!isOpen)}
        aria-label="User account menu"
        aria-expanded={isOpen}
        className="flex items-center gap-2 p-1 pl-2 pr-2.5 rounded-full bg-white border border-slate-200 hover:border-slate-300 transition-colors cursor-pointer text-left shadow-xs"
      >
        <div className="w-6 h-6 rounded-full bg-indigo-600 flex items-center justify-center text-[10px] font-bold text-white shadow-xs">
          {initials}
        </div>
        <div className="hidden sm:block text-xs">
          <div className="font-semibold text-slate-900 leading-none truncate max-w-[120px]">
            {user.display_name || user.principal_id}
          </div>
          <div className="text-[10px] text-slate-500 leading-none mt-0.5 truncate max-w-[120px]">
            {user.roles[0] || "User"}
          </div>
        </div>
      </button>

      {isOpen && (
        <div
          className="absolute right-0 mt-2 w-72 bg-white border border-slate-200 rounded-xl shadow-xl p-4 z-50 text-left text-xs space-y-3"
          role="menu"
        >
          {/* Identity Header */}
          <div className="border-b border-slate-100 pb-3">
            <div className="font-bold text-sm text-slate-900 truncate">
              {user.display_name || "Enterprise User"}
            </div>
            {user.email && (
              <div className="text-slate-500 text-xs truncate mt-0.5">{user.email}</div>
            )}
            <div className="font-mono text-[10px] text-slate-400 mt-1 truncate">
              ID: {user.principal_id}
            </div>
          </div>

          {/* Tenant & Role Details */}
          <div className="space-y-2 text-slate-700">
            <div className="flex items-center gap-2 text-slate-500">
              <Building2 className="w-3.5 h-3.5 text-indigo-600 shrink-0" />
              <span>Tenant:</span>
              <strong className="text-slate-900 font-mono">{user.tenant_id}</strong>
            </div>

            <div className="flex items-start gap-2 text-slate-500">
              <Shield className="w-3.5 h-3.5 text-emerald-600 shrink-0 mt-0.5" />
              <div>
                <span>Roles:</span>
                <div className="flex flex-wrap gap-1 mt-1">
                  {user.roles.map((role) => (
                    <span
                      key={role}
                      className="px-2 py-0.5 bg-slate-100 border border-slate-200 rounded text-[10px] text-indigo-700 font-semibold"
                    >
                      {role}
                    </span>
                  ))}
                </div>
              </div>
            </div>

            <div className="flex items-center gap-2 text-slate-500">
              <Key className="w-3.5 h-3.5 text-amber-600 shrink-0" />
              <span>Auth:</span>
              <span className="text-slate-700">{user.authentication_method}</span>
            </div>
          </div>

          {/* Logout Action */}
          <div className="pt-2 border-t border-slate-100">
            <button
              onClick={() => {
                setIsOpen(false);
                logout();
              }}
              className="w-full flex items-center gap-2 px-3 py-2 text-rose-600 hover:text-rose-700 hover:bg-rose-50 rounded-lg transition-colors cursor-pointer border-none bg-transparent font-medium"
            >
              <LogOut className="w-3.5 h-3.5" />
              <span>Sign Out</span>
            </button>
          </div>
        </div>
      )}
    </div>
  );
};

import React from "react";
import { Link } from "react-router-dom";
import { PageHeader } from "../components/ui/PageHeader";
import { Card, CardContent } from "../components/ui/Card";
import { ShieldAlert, ArrowLeft } from "lucide-react";
import { useAuth } from "../context/AuthContext";

export const ForbiddenPage: React.FC = () => {
  const { user } = useAuth();

  return (
    <div className="space-y-6">
      <PageHeader
        title="Access Restricted (403)"
        breadcrumbs={[
          { label: "Operations Engine", href: "/dashboard" },
          { label: "Access Denied" },
        ]}
      />

      <Card>
        <CardContent className="p-8 text-center max-w-lg mx-auto space-y-4">
          <div className="w-14 h-14 rounded-2xl bg-rose-950/60 border border-rose-800/60 flex items-center justify-center text-rose-400 mx-auto">
            <ShieldAlert className="w-7 h-7" />
          </div>

          <div className="space-y-1">
            <h2 className="text-xl font-bold text-white tracking-tight">Authorization Required</h2>
            <p className="text-sm text-slate-400">
              Your authenticated principal does not hold the required enterprise roles or permissions to access this screen.
            </p>
          </div>

          <div className="p-4 bg-slate-900 rounded-xl border border-slate-800 text-left text-xs space-y-1.5">
            <div className="flex justify-between">
              <span className="text-slate-400">Authenticated Principal:</span>
              <span className="font-mono text-slate-200">{user?.principal_id || "Anonymous"}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-400">Tenant Context:</span>
              <span className="font-mono text-slate-200">{user?.tenant_id || "Unassigned"}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-400">Current Roles:</span>
              <span className="font-mono text-slate-200">{user?.roles.join(", ") || "None"}</span>
            </div>
          </div>

          <div className="pt-2">
            <Link
              to="/dashboard"
              className="inline-flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-semibold no-underline transition-colors"
            >
              <ArrowLeft className="w-3.5 h-3.5" /> Return to Dashboard
            </Link>
          </div>
        </CardContent>
      </Card>
    </div>
  );
};

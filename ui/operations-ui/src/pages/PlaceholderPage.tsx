import React from "react";
import { PageHeader } from "../components/ui/PageHeader";
import { Card, CardContent } from "../components/ui/Card";
import { Badge } from "../components/ui/Badge";
import { Calendar, Layers, ShieldCheck, ArrowRight } from "lucide-react";
import { Link } from "react-router-dom";

export interface PlaceholderPageProps {
  title: string;
  moduleName: string;
  targetPhase: string;
  description: string;
  backendEndpoint?: string;
}

export const PlaceholderPage: React.FC<PlaceholderPageProps> = ({
  title,
  moduleName,
  targetPhase,
  description,
  backendEndpoint,
}) => {
  return (
    <div className="space-y-6">
      <PageHeader
        title={title}
        breadcrumbs={[
          { label: "Operations Engine", href: "/dashboard" },
          { label: moduleName, href: "#" },
          { label: title },
        ]}
        badge={<Badge variant="neutral">{targetPhase}</Badge>}
      />

      <Card>
        <CardContent className="p-8 text-center max-w-xl mx-auto space-y-5">
          <div className="w-14 h-14 rounded-2xl bg-indigo-950/60 border border-indigo-800/60 flex items-center justify-center text-indigo-400 mx-auto shadow-inner">
            <Layers className="w-7 h-7" />
          </div>

          <div className="space-y-2">
            <h2 className="text-xl font-bold text-white tracking-tight">{title} Module</h2>
            <p className="text-sm text-slate-400 leading-relaxed">{description}</p>
          </div>

          <div className="p-4 bg-slate-900/80 rounded-xl border border-slate-800 text-left space-y-2.5 text-xs">
            <div className="flex items-center justify-between text-slate-400">
              <span className="font-medium flex items-center gap-1.5">
                <Calendar className="w-3.5 h-3.5 text-indigo-400" /> Planned Delivery:
              </span>
              <span className="text-slate-200 font-semibold">{targetPhase}</span>
            </div>

            {backendEndpoint && (
              <div className="flex items-center justify-between text-slate-400">
                <span className="font-medium flex items-center gap-1.5">
                  <ShieldCheck className="w-3.5 h-3.5 text-emerald-400" /> Backend Contract:
                </span>
                <code className="text-emerald-400 bg-emerald-950/50 px-2 py-0.5 rounded font-mono text-[11px] border border-emerald-900">
                  {backendEndpoint}
                </code>
              </div>
            )}

            <div className="text-[11px] text-slate-400 pt-2 border-t border-slate-800">
              * Frontend Phase F1 establishes the authenticated application shell, design system, and live Dashboard.
              This operational screen will be bound to its verified backend endpoints in {targetPhase}.
            </div>
          </div>

          <div className="pt-2">
            <Link
              to="/dashboard"
              className="inline-flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-semibold no-underline transition-colors shadow-sm"
            >
              Return to Operations Dashboard <ArrowRight className="w-3.5 h-3.5" />
            </Link>
          </div>
        </CardContent>
      </Card>
    </div>
  );
};

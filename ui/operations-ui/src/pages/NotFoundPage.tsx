import React from "react";
import { Link } from "react-router-dom";
import { PageHeader } from "../components/ui/PageHeader";
import { Card, CardContent } from "../components/ui/Card";
import { HelpCircle, ArrowLeft } from "lucide-react";

export const NotFoundPage: React.FC = () => {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Page Not Found (404)"
        breadcrumbs={[
          { label: "Operations Engine", href: "/dashboard" },
          { label: "Not Found" },
        ]}
      />

      <Card>
        <CardContent className="p-8 text-center max-w-lg mx-auto space-y-4">
          <div className="w-14 h-14 rounded-2xl bg-slate-800/80 border border-slate-700 flex items-center justify-center text-slate-300 mx-auto">
            <HelpCircle className="w-7 h-7" />
          </div>

          <div className="space-y-1">
            <h2 className="text-xl font-bold text-white tracking-tight">Route Not Found</h2>
            <p className="text-sm text-slate-400">
              The requested navigation path is not recognized within the Operations Engine navigation map.
            </p>
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

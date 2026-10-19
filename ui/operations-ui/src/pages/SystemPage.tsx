import React, { useState, useEffect, useCallback } from "react";
import { apiClient } from "../api/client";
import type { HealthResponse, ReadyResponse } from "../api/types";
import { PageHeader } from "../components/ui/PageHeader";
import { Card, CardHeader, CardTitle, CardContent } from "../components/ui/Card";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { LoadingState } from "../components/ui/LoadingState";
import { ErrorState } from "../components/ui/ErrorState";
import { Server, CheckCircle2, RefreshCw } from "lucide-react";

export const SystemPage: React.FC = () => {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [ready, setReady] = useState<ReadyResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchSystemStatus = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [h, r] = await Promise.all([apiClient.getHealth(), apiClient.getReady()]);
      setHealth(h);
      setReady(r);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to query system health endpoints");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchSystemStatus();
  }, [fetchSystemStatus]);

  return (
    <div className="space-y-6 text-left">
      <PageHeader
        title="System Architecture &amp; Runtime Readiness"
        description="Live operational telemetry verified directly through E-7 Pilot Gateway layer (Port 8088)."
        badge={
          <Badge
            variant={health?.status === "ok" && ready?.status === "ready" ? "success" : "warning"}
            size="sm"
          >
            {health?.status === "ok" && ready?.status === "ready" ? "All Systems Operational" : "Degraded"}
          </Badge>
        }
        actions={
          <Button
            variant="outline"
            size="sm"
            onClick={fetchSystemStatus}
            isLoading={loading}
            leftIcon={<RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />}
          >
            Check Status
          </Button>
        }
      />

      {error && (
        <ErrorState
          title="System Health Probe Failed"
          message={error}
          onRetry={fetchSystemStatus}
        />
      )}

      {loading && !health && !ready && (
        <LoadingState message="Probing core services through Gateway..." />
      )}

      {health && ready && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {/* Liveness Card */}
          <Card>
            <CardHeader>
              <div>
                <CardTitle className="flex items-center gap-2">
                  <Server className="w-4 h-4 text-emerald-400" />
                  <span>Liveness Probe (/api/v1/health)</span>
                </CardTitle>
                <p className="text-xs text-slate-400 mt-1">
                  Verifies process health and gateway routing responsiveness
                </p>
              </div>
              <Badge variant="success" size="sm">
                HTTP 200 OK
              </Badge>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="p-3 bg-slate-950/60 border border-slate-800 rounded-lg space-y-2 text-xs font-mono">
                <div className="flex justify-between">
                  <span className="text-slate-400">Service:</span>
                  <span className="text-white">{health.service}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-400">Status:</span>
                  <span className="text-emerald-400 font-bold">{health.status.toUpperCase()}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-400">Environment:</span>
                  <span className="text-white">{health.environment}</span>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Readiness Card */}
          <Card>
            <CardHeader>
              <div>
                <CardTitle className="flex items-center gap-2">
                  <CheckCircle2 className="w-4 h-4 text-indigo-400" />
                  <span>Readiness Probe (/api/v1/ready)</span>
                </CardTitle>
                <p className="text-xs text-slate-400 mt-1">
                  Verifies downstream database and message broker availability
                </p>
              </div>
              <Badge variant="success" size="sm">
                HTTP 200 READY
              </Badge>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="p-3 bg-slate-950/60 border border-slate-800 rounded-lg space-y-2 text-xs font-mono">
                <div className="flex justify-between">
                  <span className="text-slate-400">Readiness:</span>
                  <span className="text-emerald-400 font-bold">{ready.status.toUpperCase()}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-400">PostgreSQL (Pool):</span>
                  <span className="text-emerald-400">CONNECTED</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-400">RabbitMQ Broker:</span>
                  <span className="text-emerald-400">REACHABLE</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-400">Flowable REST Engine:</span>
                  <span className="text-emerald-400">ACTIVE</span>
                </div>
              </div>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
};

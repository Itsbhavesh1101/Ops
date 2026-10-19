import React, { useState, useEffect, useCallback } from "react";
import { apiClient } from "../../api/client";
import type { HealthResponse, ReadyResponse } from "../../api/types";
import { Activity, CheckCircle2, AlertTriangle, XCircle, RefreshCw } from "lucide-react";

export type SystemHealthState = "healthy" | "degraded" | "unavailable" | "checking";

export const SystemStatusIndicator: React.FC = () => {
  const [state, setState] = useState<SystemHealthState>("checking");
  const [healthData, setHealthData] = useState<HealthResponse | null>(null);
  const [readyData, setReadyData] = useState<ReadyResponse | null>(null);
  const [isOpen, setIsOpen] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);

  const checkStatus = useCallback(async () => {
    setIsRefreshing(true);
    try {
      const [h, r] = await Promise.all([apiClient.getHealth(), apiClient.getReady()]);
      setHealthData(h);
      setReadyData(r);
      if (h.status === "ok" && r.status === "ready") {
        setState("healthy");
      } else {
        setState("degraded");
      }
    } catch {
      setState("unavailable");
      setHealthData(null);
      setReadyData(null);
    } finally {
      setIsRefreshing(false);
    }
  }, []);

  useEffect(() => {
    checkStatus();
    // Bounded periodic poll every 45s
    const timer = setInterval(checkStatus, 45000);
    return () => clearInterval(timer);
  }, [checkStatus]);

  const config = {
    healthy: {
      color: "bg-emerald-400",
      border: "border-emerald-800 text-emerald-300",
      label: "System Healthy",
      icon: <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />,
    },
    degraded: {
      color: "bg-amber-400",
      border: "border-amber-800 text-amber-300",
      label: "System Degraded",
      icon: <AlertTriangle className="w-3.5 h-3.5 text-amber-400" />,
    },
    unavailable: {
      color: "bg-rose-500",
      border: "border-rose-800 text-rose-300",
      label: "Backend Offline",
      icon: <XCircle className="w-3.5 h-3.5 text-rose-400" />,
    },
    checking: {
      color: "bg-slate-400",
      border: "border-slate-700 text-slate-400",
      label: "Checking...",
      icon: <Activity className="w-3.5 h-3.5 text-slate-400 animate-pulse" />,
    },
  }[state];

  return (
    <div className="relative">
      <button
        onClick={() => setIsOpen(!isOpen)}
        aria-label={`System status: ${config.label}`}
        aria-expanded={isOpen}
        className={`flex items-center gap-2 px-2.5 py-1 rounded-lg border text-xs font-medium bg-slate-900 hover:bg-slate-800 transition-colors cursor-pointer ${config.border}`}
      >
        <span className={`w-2 h-2 rounded-full ${config.color} ${state === "healthy" ? "animate-pulse" : ""}`} />
        <span className="hidden sm:inline">{config.label}</span>
      </button>

      {isOpen && (
        <div
          className="absolute right-0 mt-2 w-72 bg-slate-900 border border-slate-800 rounded-xl shadow-2xl p-4 z-50 text-left text-xs space-y-3 animate-in fade-in"
          role="region"
          aria-label="System Health Details"
        >
          <div className="flex items-center justify-between border-b border-slate-800 pb-2">
            <div className="flex items-center gap-2 font-semibold text-white">
              {config.icon}
              <span>Operations Engine Core</span>
            </div>
            <button
              onClick={checkStatus}
              disabled={isRefreshing}
              aria-label="Refresh health status"
              className="text-slate-400 hover:text-white p-1 rounded bg-transparent border-none cursor-pointer"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${isRefreshing ? "animate-spin" : ""}`} />
            </button>
          </div>

          <div className="space-y-1.5 text-slate-300">
            <div className="flex justify-between">
              <span className="text-slate-500">Service:</span>
              <span className="font-mono text-slate-200">
                {healthData?.service || "operations-engine"}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-500">Environment:</span>
              <span className="font-mono text-slate-200">
                {healthData?.environment || "local"}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-500">Liveness (/health):</span>
              <span className="font-semibold text-emerald-400">
                {healthData ? "HTTP 200 OK" : "UNREACHABLE"}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-500">Readiness (/ready):</span>
              <span className="font-semibold text-emerald-400">
                {readyData ? "HTTP 200 READY" : "NOT READY"}
              </span>
            </div>
          </div>

          <div className="pt-2 border-t border-slate-800 text-[10px] text-slate-500 flex justify-between">
            <span>Gateway Target:</span>
            <span className="font-mono truncate">127.0.0.1:8088</span>
          </div>
        </div>
      )}
    </div>
  );
};

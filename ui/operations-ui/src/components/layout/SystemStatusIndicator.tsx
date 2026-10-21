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
      color: "bg-emerald-500",
      buttonStyle: "border-slate-200 text-slate-700 hover:bg-slate-50",
      label: "System Healthy",
      icon: <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600" />,
    },
    degraded: {
      color: "bg-amber-500",
      buttonStyle: "border-amber-200 text-amber-800 bg-amber-50/50 hover:bg-amber-50",
      label: "System Degraded",
      icon: <AlertTriangle className="w-3.5 h-3.5 text-amber-600" />,
    },
    unavailable: {
      color: "bg-rose-500",
      buttonStyle: "border-rose-200 text-rose-800 bg-rose-50/50 hover:bg-rose-50",
      label: "Backend Offline",
      icon: <XCircle className="w-3.5 h-3.5 text-rose-600" />,
    },
    checking: {
      color: "bg-slate-400",
      buttonStyle: "border-slate-200 text-slate-500 hover:bg-slate-50",
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
        className={`flex items-center gap-2 px-2.5 py-1 rounded-md border text-xs font-medium bg-white transition-colors cursor-pointer shadow-xs ${config.buttonStyle}`}
      >
        <span className={`w-2 h-2 rounded-full ${config.color} shrink-0`} />
        <span className="hidden sm:inline">{config.label}</span>
      </button>

      {isOpen && (
        <div
          className="absolute right-0 mt-2 w-72 bg-white border border-slate-200 rounded-lg shadow-lg p-4 z-50 text-left text-xs space-y-3"
          role="region"
          aria-label="System Health Details"
        >
          <div className="flex items-center justify-between border-b border-slate-100 pb-2">
            <div className="flex items-center gap-2 font-semibold text-slate-900">
              {config.icon}
              <span>Operations Engine Core</span>
            </div>
            <button
              onClick={checkStatus}
              disabled={isRefreshing}
              aria-label="Refresh health status"
              className="text-slate-400 hover:text-slate-700 p-1 rounded bg-transparent border-none cursor-pointer"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${isRefreshing ? "animate-spin" : ""}`} />
            </button>
          </div>

          <div className="space-y-1.5 text-slate-700">
            <div className="flex justify-between">
              <span className="text-slate-500">Service:</span>
              <span className="font-mono text-slate-800">
                {healthData?.service || "operations-engine"}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-500">Environment:</span>
              <span className="font-mono text-slate-800">
                {healthData?.environment || "local"}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-500">Liveness (/health):</span>
              <span className="font-semibold text-emerald-700">
                {healthData ? "HTTP 200 OK" : "UNREACHABLE"}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-500">Readiness (/ready):</span>
              <span className="font-semibold text-emerald-700">
                {readyData ? "HTTP 200 READY" : "NOT READY"}
              </span>
            </div>
          </div>

          <div className="pt-2 border-t border-slate-100 text-[10px] text-slate-400 flex justify-between">
            <span>Gateway Target:</span>
            <span className="font-mono truncate">127.0.0.1:8088</span>
          </div>
        </div>
      )}
    </div>
  );
};

import React, { useState } from "react";
import { Sidebar } from "./Sidebar";
import { Header } from "./Header";
import { useAuth } from "../../context/AuthContext";
import { LoadingState } from "../ui/LoadingState";

export class ErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { hasError: boolean; error: Error | null }
> {
  constructor(props: { children: React.ReactNode }) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    console.error("UI Uncaught Error Boundary:", error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="min-h-screen bg-slate-50 flex flex-col items-center justify-center p-6 text-center text-slate-900">
          <div className="max-w-md p-6 bg-white border border-rose-200 rounded-xl shadow-lg space-y-4">
            <div className="w-10 h-10 rounded-full bg-rose-50 flex items-center justify-center text-rose-600 mx-auto font-bold text-sm">
              !
            </div>
            <h2 className="text-base font-bold text-slate-900">Application Runtime Exception</h2>
            <p className="text-xs text-rose-700">
              {this.state.error?.message || "An unexpected error occurred in the user interface."}
            </p>
            <button
              onClick={() => window.location.reload()}
              className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg text-xs font-semibold cursor-pointer border-none shadow-xs"
            >
              Reload Application
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

export const AppShell: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const { isLoading } = useAuth();

  if (isLoading) {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center">
        <LoadingState message="Initializing Operations Engine session..." />
      </div>
    );
  }

  return (
    <ErrorBoundary>
      <div className="min-h-screen bg-slate-50 text-slate-900 flex flex-col">
        {/* Skip to Main Content Link for Accessibility */}
        <a
          href="#main-content"
          className="sr-only focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:z-50 focus:px-4 focus:py-2 focus:bg-indigo-600 focus:text-white focus:rounded-lg focus:shadow-md focus:text-xs focus:font-semibold"
        >
          Skip to main content
        </a>

        {/* Navigation Sidebar */}
        <Sidebar isOpen={sidebarOpen} onClose={() => setSidebarOpen(false)} />

        {/* Content Area offset by Sidebar width on desktop (w-[232px]) */}
        <div className="flex-1 flex flex-col md:pl-[232px]">
          <Header onOpenSidebar={() => setSidebarOpen(true)} />

          <main id="main-content" className="flex-1 p-4 sm:p-6 lg:p-8 max-w-[1440px] w-full mx-auto">
            {children}
          </main>

          {/* Clean Institutional Footer */}
          <footer className="px-6 py-3 bg-white/50 border-t border-slate-200/60 text-xs text-slate-400 flex flex-wrap items-center justify-between gap-2">
            <span className="font-medium text-slate-500">E-7 Operations Engine</span>
            <span className="text-slate-400 text-[11px]">Enterprise Operations Platform</span>
          </footer>
        </div>
      </div>
    </ErrorBoundary>
  );
};

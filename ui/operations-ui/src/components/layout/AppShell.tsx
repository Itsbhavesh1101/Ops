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
        <div className="min-h-screen bg-slate-950 flex flex-col items-center justify-center p-6 text-center text-slate-100">
          <div className="max-w-md p-6 bg-slate-900 border border-rose-900/60 rounded-2xl shadow-2xl space-y-4">
            <div className="w-12 h-12 rounded-full bg-rose-950 flex items-center justify-center text-rose-400 mx-auto">
              ⚠️
            </div>
            <h2 className="text-lg font-bold text-white">Application Runtime Exception</h2>
            <p className="text-xs text-rose-300">
              {this.state.error?.message || "An unexpected error occurred in the user interface."}
            </p>
            <button
              onClick={() => window.location.reload()}
              className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-semibold cursor-pointer border-none"
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
      <div className="min-h-screen bg-slate-950 flex items-center justify-center">
        <LoadingState message="Initializing Operations Engine session..." />
      </div>
    );
  }

  return (
    <ErrorBoundary>
      <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col">
        {/* Skip to Main Content Link for Screen Readers */}
        <a
          href="#main-content"
          className="sr-only focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:z-50 focus:px-4 focus:py-2 focus:bg-indigo-600 focus:text-white focus:rounded-lg focus:shadow-lg focus:text-xs focus:font-semibold"
        >
          Skip to main content
        </a>

        {/* Navigation Sidebar */}
        <Sidebar isOpen={sidebarOpen} onClose={() => setSidebarOpen(false)} />

        {/* Content Area offset by Sidebar width on desktop */}
        <div className="flex-1 flex flex-col md:pl-64">
          <Header onOpenSidebar={() => setSidebarOpen(true)} />

          <main id="main-content" className="flex-1 p-4 md:p-6 lg:p-8 max-w-7xl w-full mx-auto">
            {children}
          </main>

          {/* Institutional Footer */}
          <footer className="px-6 py-4 bg-slate-900 border-t border-slate-800 text-xs text-slate-500 flex flex-wrap items-center justify-between gap-2">
            <span>IR Infotech E-7 Operations Engine — Production Demo Interface</span>
            <span>Connected to Canonical Real Backend (Port 8088 Gateway)</span>
          </footer>
        </div>
      </div>
    </ErrorBoundary>
  );
};

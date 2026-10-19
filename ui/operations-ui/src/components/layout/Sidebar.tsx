import React from "react";
import { Link, useLocation } from "react-router-dom";
import {
  LayoutDashboard,
  Target,
  FileSpreadsheet,
  CheckSquare,
  Inbox,
  GitBranch,
  Split,
  FormInput,
  Users,
  Clock,
  Cpu,
  CalendarDays,
  LineChart,
  ShieldCheck,
  Server,
  X,
} from "lucide-react";

export interface NavItem {
  label: string;
  href: string;
  icon: React.ReactNode;
  badge?: string;
  isPhaseF1Ready?: boolean;
}

export interface NavSection {
  title: string;
  items: NavItem[];
}

interface SidebarProps {
  isOpen: boolean;
  onClose: () => void;
}

export const NAV_SECTIONS: NavSection[] = [
  {
    title: "Core",
    items: [
      {
        label: "Dashboard",
        href: "/dashboard",
        icon: <LayoutDashboard className="w-4 h-4" />,
        isPhaseF1Ready: true,
      },
    ],
  },
  {
    title: "Planning & Intelligence",
    items: [
      {
        label: "Goals",
        href: "/goals",
        icon: <Target className="w-4 h-4" />,
        isPhaseF1Ready: true,
      },
      {
        label: "Plans",
        href: "/plans",
        icon: <FileSpreadsheet className="w-4 h-4" />,
        isPhaseF1Ready: true,
      },
      {
        label: "Tasks / WBS",
        href: "/tasks",
        icon: <CheckSquare className="w-4 h-4" />,
        isPhaseF1Ready: true,
      },
    ],
  },
  {
    title: "Workflows & Automation",
    items: [
      {
        label: "Approval Inbox",
        href: "/workflows/inbox",
        icon: <Inbox className="w-4 h-4" />,
        isPhaseF1Ready: true,
      },
      {
        label: "BPMN Modeler",
        href: "/workflows/bpmn",
        icon: <GitBranch className="w-4 h-4" />,
        isPhaseF1Ready: true,
      },
      {
        label: "DMN Decisions",
        href: "/workflows/dmn",
        icon: <Split className="w-4 h-4" />,
        isPhaseF1Ready: true,
      },
      {
        label: "Form Builder",
        href: "/workflows/forms",
        icon: <FormInput className="w-4 h-4" />,
        isPhaseF1Ready: true,
      },
    ],
  },
  {
    title: "Resources",
    items: [
      {
        label: "Resource Catalog",
        href: "/resources",
        icon: <Users className="w-4 h-4" />,
        isPhaseF1Ready: true,
      },
      {
        label: "Availability Windows",
        href: "/resources/availability",
        icon: <Clock className="w-4 h-4" />,
        isPhaseF1Ready: true,
      },
    ],
  },
  {
    title: "Scheduling Engine",
    items: [
      {
        label: "Scheduling Jobs",
        href: "/scheduling/jobs",
        icon: <Cpu className="w-4 h-4" />,
        isPhaseF1Ready: true,
      },
      {
        label: "Timetables & Schedules",
        href: "/schedules",
        icon: <CalendarDays className="w-4 h-4" />,
        isPhaseF1Ready: true,
      },
    ],
  },
  {
    title: "Operations & Governance",
    items: [
      {
        label: "Operations Analytics",
        href: "/analytics",
        icon: <LineChart className="w-4 h-4" />,
      },
      {
        label: "Audit Trail",
        href: "/audit",
        icon: <ShieldCheck className="w-4 h-4" />,
      },
    ],
  },
  {
    title: "System",
    items: [
      {
        label: "Health & Readiness",
        href: "/system",
        icon: <Server className="w-4 h-4" />,
        isPhaseF1Ready: true,
      },
    ],
  },
];

export const Sidebar: React.FC<SidebarProps> = ({ isOpen, onClose }) => {
  const location = useLocation();

  const isActive = (path: string) => {
    if (path === "/dashboard" && (location.pathname === "/" || location.pathname === "/dashboard")) {
      return true;
    }
    return location.pathname.startsWith(path);
  };

  return (
    <>
      {/* Mobile Backdrop */}
      {isOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm md:hidden"
          onClick={onClose}
          aria-hidden="true"
        />
      )}

      {/* Sidebar Panel */}
      <aside
        className={`fixed top-0 bottom-0 left-0 z-50 w-64 bg-slate-900 border-r border-slate-800 flex flex-col transition-transform duration-200 ease-in-out md:translate-x-0 ${
          isOpen ? "translate-x-0" : "-translate-x-full"
        }`}
        aria-label="Application Navigation"
      >
        {/* Brand Header */}
        <div className="h-16 px-5 border-b border-slate-800 flex items-center justify-between">
          <Link
            to="/dashboard"
            onClick={onClose}
            className="flex items-center gap-2.5 text-white no-underline focus:outline-none"
          >
            <div className="w-8 h-8 rounded-lg bg-indigo-600 flex items-center justify-center font-extrabold text-white shadow-md shadow-indigo-500/20">
              E7
            </div>
            <div className="text-left">
              <div className="text-sm font-bold tracking-tight text-white leading-none">
                Operations Engine
              </div>
              <div className="text-[10px] text-slate-400 font-mono leading-none mt-1">
                v1.0.0 Enterprise
              </div>
            </div>
          </Link>
          <button
            onClick={onClose}
            aria-label="Close navigation"
            className="md:hidden text-slate-400 hover:text-white p-1 rounded bg-transparent border-none cursor-pointer"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Scrollable Nav Items */}
        <nav className="flex-1 overflow-y-auto px-3 py-4 space-y-5 text-left">
          {NAV_SECTIONS.map((section) => (
            <div key={section.title} className="space-y-1">
              <div className="px-3 text-[11px] font-bold text-slate-400 uppercase tracking-wider">
                {section.title}
              </div>
              <div className="space-y-0.5">
                {section.items.map((item) => {
                  const active = isActive(item.href);
                  return (
                    <Link
                      key={item.href}
                      to={item.href}
                      onClick={onClose}
                      className={`flex items-center justify-between px-3 py-2 rounded-lg text-xs font-medium transition-colors no-underline select-none ${
                        active
                          ? "bg-indigo-600/15 text-indigo-400 font-semibold border-l-2 border-indigo-500"
                          : "text-slate-400 hover:text-slate-200 hover:bg-slate-800/60"
                      }`}
                    >
                      <div className="flex items-center gap-2.5 truncate">
                        <span className={active ? "text-indigo-400" : "text-slate-400"}>
                          {item.icon}
                        </span>
                        <span className="truncate">{item.label}</span>
                      </div>
                      {item.isPhaseF1Ready ? (
                        <span className="text-[9px] px-1.5 py-0.2 rounded bg-emerald-950/80 text-emerald-400 border border-emerald-900 font-mono">
                          LIVE
                        </span>
                      ) : (
                        <span className="text-[9px] px-1.5 py-0.2 rounded bg-slate-800/60 text-slate-400 font-mono">
                          F2/F3
                        </span>
                      )}
                    </Link>
                  );
                })}
              </div>
            </div>
          ))}
        </nav>

        {/* Sidebar Footer */}
        <div className="p-3 border-t border-slate-800 text-[11px] text-slate-400 text-left flex items-center justify-between">
          <span>IR Infotech Engine</span>
          <span className="font-mono text-emerald-400">● Real API</span>
        </div>
      </aside>
    </>
  );
};

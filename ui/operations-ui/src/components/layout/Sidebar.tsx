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
  ariaLabel?: string;
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
    title: "Overview",
    items: [
      {
        label: "Dashboard",
        href: "/dashboard",
        icon: <LayoutDashboard className="w-4 h-4" />,
      },
    ],
  },
  {
    title: "Planning",
    items: [
      {
        label: "Goals",
        href: "/goals",
        icon: <Target className="w-4 h-4" />,
      },
      {
        label: "Plans",
        href: "/plans",
        icon: <FileSpreadsheet className="w-4 h-4" />,
      },
      {
        label: "Tasks / WBS",
        href: "/tasks",
        icon: <CheckSquare className="w-4 h-4" />,
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
      },
      {
        label: "Availability",
        href: "/resources/availability",
        icon: <Clock className="w-4 h-4" />,
      },
    ],
  },
  {
    title: "Scheduling",
    items: [
      {
        label: "Solver Jobs",
        href: "/scheduling/jobs",
        icon: <Cpu className="w-4 h-4" />,
        ariaLabel: "Scheduling Jobs",
      },
      {
        label: "Schedules",
        href: "/schedules",
        icon: <CalendarDays className="w-4 h-4" />,
        ariaLabel: "Timetables & Schedules",
      },
    ],
  },
  {
    title: "Operations",
    items: [
      {
        label: "Workflow Inbox",
        href: "/workflows/inbox",
        icon: <Inbox className="w-4 h-4" />,
        ariaLabel: "Approval Inbox",
      },
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
    title: "Workflow Modeler",
    items: [
      {
        label: "BPMN Modeler",
        href: "/workflows/bpmn",
        icon: <GitBranch className="w-4 h-4" />,
      },
      {
        label: "DMN Decisions",
        href: "/workflows/dmn",
        icon: <Split className="w-4 h-4" />,
      },
      {
        label: "Form Builder",
        href: "/workflows/forms",
        icon: <FormInput className="w-4 h-4" />,
      },
    ],
  },
  {
    title: "System",
    items: [
      {
        label: "System Status",
        href: "/system",
        icon: <Server className="w-4 h-4" />,
        ariaLabel: "Health & Readiness",
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
          className="fixed inset-0 z-40 bg-slate-900/30 backdrop-blur-xs md:hidden"
          onClick={onClose}
          aria-hidden="true"
        />
      )}

      {/* Sidebar Panel: Exactly 232px width */}
      <aside
        className={`fixed top-0 bottom-0 left-0 z-50 w-[232px] bg-white border-r border-slate-200/80 flex flex-col transition-transform duration-200 ease-in-out md:translate-x-0 ${
          isOpen ? "translate-x-0" : "-translate-x-full md:translate-x-0"
        }`}
        aria-label="Application Navigation"
      >
        {/* Brand Header */}
        <div className="h-14 px-4 border-b border-slate-100 flex items-center justify-between">
          <Link
            to="/dashboard"
            onClick={onClose}
            className="flex items-center gap-2.5 no-underline focus:outline-none"
          >
            <div className="w-6 h-6 rounded-md bg-indigo-600 flex items-center justify-center font-bold text-white text-[11px] shadow-xs shrink-0 tracking-tight">
              E7
            </div>
            <div className="text-left">
              <div className="text-[13px] font-semibold tracking-tight text-slate-900 leading-none">
                Operations Engine
              </div>
              <div className="text-[10px] text-slate-400 font-medium leading-none mt-1">
                Enterprise B2B
              </div>
            </div>
          </Link>
          <button
            onClick={onClose}
            aria-label="Close navigation"
            className="md:hidden text-slate-400 hover:text-slate-700 p-1 rounded-md bg-transparent border-none cursor-pointer"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Scrollable Nav Items */}
        <nav className="flex-1 overflow-y-auto px-2 py-3 space-y-3.5 text-left">
          {NAV_SECTIONS.map((section) => (
            <div key={section.title} className="space-y-0.5">
              <div className="px-2.5 py-1 text-[10px] font-semibold text-slate-400 uppercase tracking-wider">
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
                      aria-label={item.ariaLabel || item.label}
                      className={`flex items-center gap-2.5 px-2.5 py-1.5 rounded-md text-xs font-medium transition-colors no-underline select-none ${
                        active
                          ? "bg-indigo-50/80 text-indigo-700 font-semibold"
                          : "text-slate-600 hover:text-slate-900 hover:bg-slate-50"
                      }`}
                    >
                      <span className={active ? "text-indigo-600" : "text-slate-400"}>
                        {item.icon}
                      </span>
                      <span className="truncate">{item.label}</span>
                    </Link>
                  );
                })}
              </div>
            </div>
          ))}
        </nav>

        {/* Clean Minimal Footer */}
        <div className="p-3 border-t border-slate-100 text-[11px] text-slate-400 text-left flex items-center justify-between">
          <span className="font-medium text-slate-500">E-7 Operations</span>
          <span className="text-emerald-600 font-medium flex items-center gap-1.5 text-[10px]">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 inline-block"></span>
            Online
          </span>
        </div>
      </aside>
    </>
  );
};

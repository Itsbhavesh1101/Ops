/**
 * Consistent formatting utilities for presentation and status language normalization.
 * Backend values remain canonical internally; display labels are formatted for operators.
 */

const STATUS_DISPLAY_MAP: Record<string, string> = {
  // Goal statuses
  ACTIVE: "Active",
  ACHIEVED: "Achieved",
  DRAFT: "Draft",
  CANCELLED: "Cancelled",

  // Plan statuses
  GENERATED: "Generated",
  VALIDATED: "Validated",
  APPROVAL_PENDING: "Approval Pending",
  APPROVED: "Approved",
  REJECTED: "Rejected",
  SUPERSEDED: "Superseded",

  // Task statuses
  TODO: "To Do",
  IN_PROGRESS: "In Progress",
  DONE: "Done",
  BLOCKED: "Blocked",

  // Scheduling Job statuses
  REQUESTED: "Requested",
  QUEUED: "Queued",
  CLAIMED: "Claimed",
  SOLVING: "Solving",
  FEASIBLE: "Feasible",
  INFEASIBLE: "Infeasible",
  FAILED: "Failed",

  // Schedule lifecycle statuses
  REVIEW: "Review",
  PUBLISHED: "Published",
  ARCHIVED: "Archived",

  // Resource statuses and types
  INACTIVE: "Inactive",
  MAINTENANCE: "Maintenance",
  PERSON: "Person",
  EQUIPMENT: "Equipment",
  ROOM: "Room",
  FACILITY: "Facility",
};

/**
 * Format any raw uppercase/snake_case backend status into title-cased, human-readable status text.
 */
export function formatStatus(status: string | null | undefined): string {
  if (!status) return "Unknown";
  const upper = status.trim().toUpperCase();
  if (STATUS_DISPLAY_MAP[upper]) {
    return STATUS_DISPLAY_MAP[upper];
  }
  // Fallback: replace underscores with spaces and title case each word
  return upper
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(" ");
}

/**
 * Format ISO datetime string to localized, readable operator date and time.
 */
export function formatDateTime(isoString: string | null | undefined): string {
  if (!isoString) return "—";
  try {
    const d = new Date(isoString);
    if (isNaN(d.getTime())) return isoString;
    return d.toLocaleString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return isoString;
  }
}

/**
 * Format ISO date string to readable operator date.
 */
export function formatDate(isoString: string | null | undefined): string {
  if (!isoString) return "—";
  try {
    const d = new Date(isoString);
    if (isNaN(d.getTime())) return isoString;
    return d.toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  } catch {
    return isoString;
  }
}

/**
 * Format ISO datetime interval into a single readable date and time range.
 * Example: "21 Oct 2026, 1:30 PM – 11:30 PM"
 */
export function formatDateTimeRange(
  startIso: string | null | undefined,
  endIso: string | null | undefined
): string {
  if (!startIso) return "—";
  try {
    const start = new Date(startIso);
    if (isNaN(start.getTime())) return startIso;
    const datePart = start.toLocaleDateString(undefined, {
      day: "numeric",
      month: "short",
      year: "numeric",
    });
    const startTimePart = start.toLocaleTimeString(undefined, {
      hour: "numeric",
      minute: "2-digit",
    });
    if (!endIso) return `${datePart}, ${startTimePart}`;
    const end = new Date(endIso);
    if (isNaN(end.getTime())) return `${datePart}, ${startTimePart}`;
    const sameDay =
      start.getFullYear() === end.getFullYear() &&
      start.getMonth() === end.getMonth() &&
      start.getDate() === end.getDate();
    const endTimePart = end.toLocaleTimeString(undefined, {
      hour: "numeric",
      minute: "2-digit",
    });
    if (sameDay) {
      return `${datePart}, ${startTimePart} – ${endTimePart}`;
    }
    const endDatePart = end.toLocaleDateString(undefined, {
      day: "numeric",
      month: "short",
      year: "numeric",
    });
    return `${datePart} ${startTimePart} – ${endDatePart} ${endTimePart}`;
  } catch {
    return startIso;
  }
}

/**
 * Format technical UUID into a concise copyable reference while keeping readability.
 */
export function formatShortId(id: string | null | undefined, length: number = 8): string {
  if (!id) return "—";
  if (id.length <= length) return id;
  return `${id.substring(0, length)}...`;
}

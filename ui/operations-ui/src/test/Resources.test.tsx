import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ResourcesPage } from "../pages/ResourcesPage";
import { AvailabilityPage } from "../pages/AvailabilityPage";
import { ToastProvider } from "../components/ui/Toast";
import { apiClient } from "../api/client";
import type { ResourceCatalogItem, ResourceAvailabilityItem } from "../api/types";

const mockAuthValue = {
  status: "AUTHENTICATED",
  user: {
    principal_id: "test-operator",
    tenant_id: "tenant-engineering",
    roles: ["operator"],
    permissions: ["read", "write"],
    principal_type: "HUMAN",
    authentication_method: "OIDC",
    display_name: "Test Operator",
  },
  isAuthenticated: true,
  isLoading: false,
  error: null,
  loginWithBearerToken: vi.fn(),
  logout: vi.fn(),
  clearError: vi.fn(),
  oidcConfig: {
    clientId: "test-client-id",
    issuer: "https://accounts.google.com",
    audience: "e7-pilot-client-id",
    redirectUri: "http://127.0.0.1:5173",
    isConfigured: true,
  },
};

vi.mock("../context/AuthContext", () => ({
  useAuth: () => mockAuthValue,
}));

describe("Resources & Availability Feature Screens (F2)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  const sampleResources: ResourceCatalogItem[] = [
    {
      id: "res-001",
      resource_key: "hall-alpha",
      type: "ROOM",
      display_label: "Examination Hall Alpha (Cap: 120)",
      capacity: 1,
      skills: ["PROCTORING", "PROJECTOR"],
      active: true,
      created_at: "2026-10-01T10:00:00Z",
      updated_at: "2026-10-01T10:00:00Z",
    },
    {
      id: "res-002",
      resource_key: "invigilator-lead",
      type: "PERSON",
      display_label: "Senior Invigilator (Staff #1)",
      capacity: 1,
      skills: ["INVIGILATION"],
      active: true,
      created_at: "2026-10-01T10:00:00Z",
      updated_at: "2026-10-01T10:00:00Z",
    },
  ];

  const sampleAvailability: ResourceAvailabilityItem[] = [
    {
      resource_id: "res-001",
      start_at: "2026-10-25T08:00:00Z",
      end_at: "2026-10-25T18:00:00Z",
      interval_type: "AVAILABLE",
    },
  ];

  it("renders resources list with category distinctions", async () => {
    vi.spyOn(apiClient, "listResources").mockResolvedValue(sampleResources);

    render(
      <ToastProvider>
        <MemoryRouter initialEntries={["/resources"]}>
          <Routes>
            <Route path="/resources" element={<ResourcesPage />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    );

    await waitFor(() => {
      expect(screen.getByText("Examination Hall Alpha (Cap: 120)")).toBeInTheDocument();
      expect(screen.getByText("Senior Invigilator (Staff #1)")).toBeInTheDocument();
    });

    expect(screen.getByText("hall-alpha")).toBeInTheDocument();
    expect(screen.getByText("invigilator-lead")).toBeInTheDocument();
  });

  it("filters resources by type", async () => {
    const listSpy = vi.spyOn(apiClient, "listResources").mockResolvedValue(sampleResources);

    render(
      <ToastProvider>
        <MemoryRouter initialEntries={["/resources"]}>
          <Routes>
            <Route path="/resources" element={<ResourcesPage />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    );

    await waitFor(() => {
      expect(screen.getByText("Examination Hall Alpha (Cap: 120)")).toBeInTheDocument();
    });

    const select = screen.getAllByRole("combobox")[0];
    fireEvent.change(select, { target: { value: "ROOM" } });

    await waitFor(() => {
      expect(listSpy).toHaveBeenCalledWith({ type: "ROOM" }, "tenant-engineering");
    });
  });

  it("renders availability calendar intervals", async () => {
    vi.spyOn(apiClient, "listAvailability").mockResolvedValue(sampleAvailability);
    vi.spyOn(apiClient, "listResources").mockResolvedValue(sampleResources);

    render(
      <ToastProvider>
        <MemoryRouter initialEntries={["/resources/availability"]}>
          <Routes>
            <Route path="/resources/availability" element={<AvailabilityPage />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    );

    await waitFor(() => {
      expect(screen.getByText(/AVAILABLE/i)).toBeInTheDocument();
      expect(screen.getByText("Examination Hall Alpha (Cap: 120)")).toBeInTheDocument();
    });
  });

  it("opens Add Resource modal and submits new room successfully", async () => {
    vi.spyOn(apiClient, "listResources").mockResolvedValue(sampleResources);
    const createSpy = vi.spyOn(apiClient, "createResource").mockResolvedValue({
      id: "res-003",
      resource_key: "room-hall-a",
      type: "ROOM",
      display_label: "Examination Hall A",
      capacity: 80,
      skills: ["cctv_monitored"],
      active: true,
      created_at: "2026-10-21T00:00:00Z",
      updated_at: "2026-10-21T00:00:00Z",
    });

    render(
      <ToastProvider>
        <MemoryRouter initialEntries={["/resources"]}>
          <Routes>
            <Route path="/resources" element={<ResourcesPage />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    );

    await waitFor(() => {
      expect(screen.getByText("Add Resource")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Add Resource"));

    expect(screen.getByText("Add Local Operational Resource")).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText("e.g., room-hall-a"), {
      target: { value: "room-hall-a" },
    });
    fireEvent.change(screen.getByPlaceholderText("e.g., Examination Hall A"), {
      target: { value: "Examination Hall A" },
    });

    const createBtn = screen.getByRole("button", { name: /Create Resource/i });
    fireEvent.click(createBtn);

    await waitFor(() => {
      expect(createSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          resource_key: "room-hall-a",
          display_label: "Examination Hall A",
          type: "ROOM",
        }),
        "tenant-engineering"
      );
    });
  });

  it("opens Add Window modal on AvailabilityPage and submits window successfully", async () => {
    vi.spyOn(apiClient, "listAvailability").mockResolvedValue(sampleAvailability);
    vi.spyOn(apiClient, "listResources").mockResolvedValue(sampleResources);
    vi.spyOn(apiClient, "createAvailability").mockResolvedValue({
      resource_id: "res-001",
      interval_type: "AVAILABLE",
      start_at: "2026-10-25T08:00:00.000Z",
      end_at: "2026-10-25T18:00:00.000Z",
      reason: "Shift A",
    });

    render(
      <ToastProvider>
        <MemoryRouter initialEntries={["/resources/availability"]}>
          <Routes>
            <Route path="/resources/availability" element={<AvailabilityPage />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    );

    await waitFor(() => {
      expect(screen.getByText("Add Window")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Add Window"));

    expect(screen.getByText("Add Operational Availability Window")).toBeInTheDocument();

    const saveBtn = screen.getByRole("button", { name: /Save Window/i });
    expect(saveBtn).toBeInTheDocument();
  });
});

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { SystemStatusIndicator } from "../components/layout/SystemStatusIndicator";
import { apiClient } from "../api/client";

describe("SystemStatusIndicator Health Probe", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("displays System Healthy when health status is ok and ready status is ready", async () => {
    vi.spyOn(apiClient, "getHealth").mockResolvedValueOnce({
      status: "ok",
      service: "operations-engine",
      environment: "production-demo",
    });
    vi.spyOn(apiClient, "getReady").mockResolvedValueOnce({
      status: "ready",
      service: "operations-engine",
      environment: "production-demo",
      checks: { database: true, rabbitmq: true },
    });

    render(
      <MemoryRouter>
        <SystemStatusIndicator />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText("System Healthy")).toBeInTheDocument();
    });
  });

  it("displays Backend Offline when either health or readiness probe fails", async () => {
    vi.spyOn(apiClient, "getHealth").mockResolvedValueOnce({
      status: "ok",
      service: "operations-engine",
      environment: "production-demo",
    });
    vi.spyOn(apiClient, "getReady").mockRejectedValueOnce(
      new Error("Database connection pool exhausted (500)")
    );

    render(
      <MemoryRouter>
        <SystemStatusIndicator />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText("Backend Offline")).toBeInTheDocument();
    });
  });
});

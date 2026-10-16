"""Resource usage sampler for Phase 8C performance benchmarking.

Periodically collects process-level (CPU, RSS) and container-level metrics
using psutil and docker stats without altering runtime heuristics.
Uses persistent psutil.Process objects to ensure accurate non-zero CPU percent calculations.
"""

import asyncio
import json
import logging
import subprocess
import time
from typing import Any

import psutil

logger = logging.getLogger(__name__)


class ResourceSampler:
    """Samples CPU and RSS metrics for designated process IDs and Docker containers."""

    def __init__(
        self,
        target_pids: list[int] | None = None,
        container_names: list[str] | None = None,
        interval_seconds: float = 0.5,
    ) -> None:
        self.target_pids = target_pids or []
        self.container_names = container_names or ["e7-postgres", "e7-rabbitmq", "e7-flowable"]
        self.interval = interval_seconds
        self._running = False
        self._task: asyncio.Task[None] | None = None

        self.samples: list[dict[str, Any]] = []
        self._proc_cache: dict[int, psutil.Process] = {}
        self._runner_proc: psutil.Process | None = None
        self._docker_task: asyncio.Task[None] | None = None
        self._cached_container_data: dict[str, Any] = {}

    def _get_or_create_process(self, pid: int) -> psutil.Process | None:
        """Retrieve persistent Process instance or instantiate and prime a new one."""
        p = self._proc_cache.get(pid)
        if p is not None:
            try:
                if p.is_running():
                    return p
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
            self._proc_cache.pop(pid, None)

        try:
            p = psutil.Process(pid)
            p.cpu_percent(interval=None)  # Prime baseline counter
            self._proc_cache[pid] = p
            return p
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None

    async def _docker_loop(self) -> None:
        """Throttled independent loop collecting Docker container resource stats."""
        while self._running:
            try:
                raw = await asyncio.to_thread(
                    subprocess.check_output,
                    [
                        "docker",
                        "stats",
                        "--no-stream",
                        "--format",
                        "{{json .}}",
                        *self.container_names,
                    ],
                    stderr=subprocess.DEVNULL,
                    text=True,
                )
                parsed: dict[str, Any] = {}
                for line in raw.strip().splitlines():
                    if line:
                        entry = json.loads(line)
                        c_name = entry.get("Name", "unknown")
                        cpu_str = entry.get("CPUPerc", "0%").rstrip("%")
                        mem_str = entry.get("MemUsage", "0MiB / 0MiB").split("/")[0].strip()
                        mem_val = 0.0
                        if "GiB" in mem_str:
                            mem_val = float(mem_str.replace("GiB", "").strip()) * 1024.0
                        elif "MiB" in mem_str:
                            mem_val = float(mem_str.replace("MiB", "").strip())
                        elif "KiB" in mem_str:
                            mem_val = float(mem_str.replace("KiB", "").strip()) / 1024.0

                        parsed[c_name] = {
                            "cpu_pct": float(cpu_str) if cpu_str else 0.0,
                            "mem_mb": round(mem_val, 2),
                        }
                self._cached_container_data = parsed
            except Exception:
                pass
            await asyncio.sleep(3.0)

    async def _sample_loop(self) -> None:
        """Sample process CPU and memory on high-resolution intervals without blocking."""
        while self._running:
            await asyncio.sleep(self.interval)
            sample_time = time.time()
            data: dict[str, Any] = {
                "timestamp": sample_time,
                "processes": {},
                "containers": dict(self._cached_container_data),
            }

            # 1. Sample target processes (e.g. API server and its worker children)
            api_cpu_sum = 0.0
            api_rss_sum = 0.0
            api_proc_active = False

            for pid in self.target_pids:
                parent = self._get_or_create_process(pid)
                if not parent:
                    continue

                procs = [parent]
                try:
                    for child in parent.children(recursive=True):
                        c_proc = self._get_or_create_process(child.pid)
                        if c_proc:
                            procs.append(c_proc)
                except Exception:
                    pass

                for proc in procs:
                    try:
                        api_cpu_sum += proc.cpu_percent(interval=None)
                        api_rss_sum += proc.memory_info().rss / (1024 * 1024)
                        api_proc_active = True
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass

            if api_proc_active:
                data["processes"]["api_process"] = {
                    "name": "e7_uvicorn_api",
                    "cpu_pct": round(api_cpu_sum, 2),
                    "rss_mb": round(api_rss_sum, 2),
                }

            # 2. Sample benchmark runner process itself using persistent instance
            if self._runner_proc:
                try:
                    data["processes"]["runner"] = {
                        "name": "benchmark_runner",
                        "cpu_pct": round(self._runner_proc.cpu_percent(interval=None), 2),
                        "rss_mb": round(self._runner_proc.memory_info().rss / (1024 * 1024), 2),
                    }
                except Exception:
                    pass

            self.samples.append(data)

    def start(self) -> None:
        """Start the background sampling loop and prime all initial process counters."""
        self._running = True
        self.samples = []
        self._proc_cache.clear()
        self._cached_container_data = {}

        # Prime target processes and existing children
        for pid in self.target_pids:
            parent = self._get_or_create_process(pid)
            if parent:
                try:
                    for child in parent.children(recursive=True):
                        self._get_or_create_process(child.pid)
                except Exception:
                    pass

        # Prime runner process
        try:
            self._runner_proc = psutil.Process()
            self._runner_proc.cpu_percent(interval=None)
        except Exception:
            self._runner_proc = None

        self._task = asyncio.create_task(self._sample_loop())
        self._docker_task = asyncio.create_task(self._docker_loop())

    async def stop(self) -> dict[str, Any]:
        """Stop sampling and compute aggregate statistics."""
        self._running = False
        for t in (self._task, self._docker_task):
            if t:
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass

        return self.compute_summary()

    def compute_summary(self) -> dict[str, Any]:
        """Compute mean and peak metrics across all recorded samples."""
        if not self.samples:
            return {"sample_count": 0}

        summary: dict[str, Any] = {
            "sample_count": len(self.samples),
            "processes": {},
            "containers": {},
        }

        # Process summaries
        p_keys = set()
        for s in self.samples:
            p_keys.update(s["processes"].keys())

        for pk in p_keys:
            cpus = [s["processes"][pk]["cpu_pct"] for s in self.samples if pk in s["processes"]]
            mems = [s["processes"][pk]["rss_mb"] for s in self.samples if pk in s["processes"]]
            if cpus and mems:
                summary["processes"][pk] = {
                    "mean_cpu_pct": round(sum(cpus) / len(cpus), 2),
                    "peak_cpu_pct": round(max(cpus), 2),
                    "mean_rss_mb": round(sum(mems) / len(mems), 2),
                    "peak_rss_mb": round(max(mems), 2),
                }

        # Container summaries
        c_keys = set()
        for s in self.samples:
            c_keys.update(s["containers"].keys())

        for ck in c_keys:
            cpus = [s["containers"][ck]["cpu_pct"] for s in self.samples if ck in s["containers"]]
            mems = [s["containers"][ck]["mem_mb"] for s in self.samples if ck in s["containers"]]
            if cpus and mems:
                summary["containers"][ck] = {
                    "mean_cpu_pct": round(sum(cpus) / len(cpus), 2),
                    "peak_cpu_pct": round(max(cpus), 2),
                    "mean_mem_mb": round(sum(mems) / len(mems), 2),
                    "peak_mem_mb": round(max(mems), 2),
                }

        return summary

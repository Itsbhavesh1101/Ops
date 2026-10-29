"""Flowable REST API Workflow Adapter implementation (Phase 3A).

Provides:
- Asynchronous HTTP client communicating with Flowable OSS REST API
- Bounded timeouts and safe idempotent retries
- Conversion between E-7 domain types and Flowable REST payloads
- Zero leakage of raw Flowable JSON or Java types into public caller surface
"""

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.telemetry.flowable import get_flowable_telemetry
from app.workflow.adapters.dto import (
    EngineDeploymentResult,
    EngineHistoricActivity,
    EngineProcessInstance,
    EngineTask,
    EngineTaskCompletionResult,
)
from app.workflow.adapters.flowable.config import FlowableSettings
from app.workflow.adapters.protocol import (
    WorkflowAdapterProtocol,
    WorkflowEngineConnectionError,
    WorkflowEngineResponseError,
    WorkflowResourceNotFoundError,
)

logger = logging.getLogger(__name__)


def dict_to_flowable_variables(variables: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Convert a Python dictionary of variables into Flowable's list-of-dicts representation."""
    if not variables:
        return []
    result = []
    for k, v in variables.items():
        result.append({"name": str(k), "value": v})
    return result


def flowable_variables_to_dict(var_list: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Convert Flowable's list-of-dicts representation into a Python dictionary."""
    if not var_list:
        return {}
    return {item["name"]: item.get("value") for item in var_list if "name" in item}


def parse_flowable_datetime(val: str | None) -> datetime | None:
    """Parse Flowable ISO-8601 timestamp into a timezone-aware datetime."""
    if not val:
        return None
    try:
        # Handle trailing Z or offsets
        dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except Exception:
        return None


class FlowableAdapter(WorkflowAdapterProtocol):
    """Asynchronous HTTP adapter connecting to Flowable REST API."""

    def __init__(
        self,
        settings: FlowableSettings | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if settings is None:
            self.settings = FlowableSettings.from_env()
        else:
            self.settings = settings
        self._custom_client = client
        self._shared_client: httpx.AsyncClient | None = None
        self._telemetry = get_flowable_telemetry()

    def _create_client(self) -> httpx.AsyncClient:
        """Create a new pooled AsyncClient with bounded connection limits."""
        auth: httpx.Auth | None = None
        headers: dict[str, str] = {}
        if self.settings.auth_mode == "basic":
            assert self.settings.username is not None
            assert self.settings.password is not None
            auth = httpx.BasicAuth(
                self.settings.username,
                self.settings.password.get_secret_value(),
            )
        elif self.settings.auth_mode == "bearer":
            assert self.settings.token is not None
            headers["Authorization"] = f"Bearer {self.settings.token.get_secret_value()}"

        limits = httpx.Limits(
            max_connections=getattr(self.settings, "pool_max_connections", 20),
            max_keepalive_connections=getattr(self.settings, "pool_max_keepalive_connections", 10),
            keepalive_expiry=30.0,
        )
        return httpx.AsyncClient(
            auth=auth,
            headers=headers if headers else None,
            timeout=httpx.Timeout(
                self.settings.timeout_seconds,
                pool=getattr(self.settings, "pool_timeout_seconds", 10.0),
            ),
            limits=limits,
        )

    def _get_client(self) -> httpx.AsyncClient:
        """Return persistent pooled httpx client or custom client."""
        if self._custom_client is not None:
            return self._custom_client
        if self._shared_client is None or self._shared_client.is_closed:
            self._shared_client = self._create_client()
        return self._shared_client

    async def close(self) -> None:
        """Close underlying HTTP client and release connection pool."""
        if self._custom_client is not None:
            await self._custom_client.aclose()
        if self._shared_client is not None:
            if not self._shared_client.is_closed:
                await self._shared_client.aclose()
            self._shared_client = None

    async def __aenter__(self) -> "FlowableAdapter":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    def _classify_operation(self, method: str, path: str) -> str:
        """Classify Flowable REST path into bounded low-cardinality operation label."""
        p = path.lower()
        m = method.upper()
        if "deployment" in p:
            return "deploy"
        if "process-instance" in p:
            return "start" if m == "POST" else "sync"
        if "task" in p:
            return "task_complete" if m == "POST" else "task_query"
        if "history" in p or "management" in p:
            return "sync"
        return "other"

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
        files: Any | None = None,
        data: Any | None = None,
        is_idempotent: bool = False,
    ) -> httpx.Response:
        """Execute HTTP request with safe error wrapping and bounded retry for idempotent calls."""
        client = self._get_client()
        retries = self.settings.max_retries if is_idempotent else 0
        if path.startswith(("http://", "https://")):
            full_url = path
        else:
            full_url = f"{self.settings.base_url.rstrip('/')}/{path.lstrip('/')}"

        op = self._classify_operation(method, path)
        start_time = time.monotonic()
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                resp = await client.request(
                    method=method,
                    url=full_url,
                    params=params,
                    json=json,
                    files=files,
                    data=data,
                )
                duration = time.monotonic() - start_time
                self._telemetry.record_roundtrip(op, duration, success=resp.is_success)
                return resp
            except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
                if attempt < retries:
                    await asyncio.sleep(0.2 * (2**attempt))
                else:
                    duration = time.monotonic() - start_time
                    self._telemetry.record_roundtrip(op, duration, success=False)
                    raise WorkflowEngineConnectionError(
                        f"Failed to connect to Flowable engine at '{self.settings.base_url}': {exc}"
                    ) from exc
            except Exception as exc:
                duration = time.monotonic() - start_time
                self._telemetry.record_roundtrip(op, duration, success=False)
                raise WorkflowEngineResponseError(f"Unexpected HTTP client error: {exc}") from exc

        duration = time.monotonic() - start_time
        self._telemetry.record_roundtrip(op, duration, success=False)
        raise WorkflowEngineConnectionError(
            f"Flowable request failed after {retries} retries: {last_error}"
        )

    async def deploy_definition(
        self,
        bpmn_xml: str,
        resource_name: str,
        tenant_id: str,
    ) -> EngineDeploymentResult:
        """Deploy a BPMN 2.0 XML definition to the Flowable engine."""
        ext = ".bpmn20.xml"
        fname = resource_name if resource_name.endswith(ext) else f"{resource_name}{ext}"
        files = {
            "file": (
                fname,
                bpmn_xml.encode("utf-8"),
                "application/xml",
            )
        }
        data = {"tenantId": tenant_id}

        resp = await self._request("POST", "/repository/deployments", files=files, data=data)
        if resp.status_code not in (200, 201):
            raise WorkflowEngineResponseError(
                f"Flowable deployment failed with status {resp.status_code}: {resp.text}"
            )

        payload = resp.json()
        deployment_id = str(payload.get("id"))
        dep_name = str(payload.get("name", resource_name))
        dep_time = parse_flowable_datetime(payload.get("deploymentTime"))

        # Query process definition created by this deployment
        def_resp = await self._request(
            "GET",
            "/repository/process-definitions",
            params={"deploymentId": deployment_id, "tenantId": tenant_id},
            is_idempotent=True,
        )
        engine_def_id = None
        engine_def_key = None
        version = None
        if def_resp.status_code == 200:
            def_data = def_resp.json().get("data", [])
            if def_data:
                first_def = def_data[0]
                engine_def_id = str(first_def.get("id"))
                engine_def_key = str(first_def.get("key"))
                version = int(first_def.get("version", 1))

        return EngineDeploymentResult(
            deployment_id=deployment_id,
            name=dep_name,
            deployment_time=dep_time,
            engine_definition_id=engine_def_id,
            engine_definition_key=engine_def_key,
            version=version,
        )

    async def start_instance(
        self,
        process_definition_key: str,
        business_key: str | None,
        variables: dict[str, Any] | None,
        tenant_id: str,
    ) -> EngineProcessInstance:
        """Start a new process instance on the workflow engine."""
        body: dict[str, Any] = {
            "processDefinitionKey": process_definition_key,
            "tenantId": tenant_id,
            "variables": dict_to_flowable_variables(variables),
        }
        if business_key:
            body["businessKey"] = business_key

        resp = await self._request("POST", "/runtime/process-instances", json=body)
        if resp.status_code not in (200, 201):
            raise WorkflowEngineResponseError(
                f"Failed to start Flowable process instance for key '{process_definition_key}': "
                f"status {resp.status_code}: {resp.text}"
            )

        payload = resp.json()
        engine_inst_id = str(payload.get("id"))
        engine_def_id = str(payload.get("processDefinitionId", ""))
        engine_def_key = str(payload.get("processDefinitionKey", process_definition_key))
        is_ended = bool(payload.get("ended", False))
        is_suspended = bool(payload.get("suspended", False))
        start_time = parse_flowable_datetime(payload.get("startTime"))

        return EngineProcessInstance(
            engine_instance_id=engine_inst_id,
            engine_definition_id=engine_def_id,
            engine_definition_key=engine_def_key,
            business_key=business_key,
            is_ended=is_ended,
            is_suspended=is_suspended,
            start_time=start_time,
            variables=variables or {},
        )

    async def get_instance(
        self,
        engine_instance_id: str,
        tenant_id: str,
    ) -> EngineProcessInstance | None:
        """Retrieve an active or finished process instance by engine ID."""
        resp = await self._request(
            "GET",
            f"/runtime/process-instances/{engine_instance_id}",
            params={"tenantId": tenant_id},
            is_idempotent=True,
        )
        if resp.status_code == 200:
            payload = resp.json()
            return EngineProcessInstance(
                engine_instance_id=str(payload.get("id")),
                engine_definition_id=str(payload.get("processDefinitionId", "")),
                engine_definition_key=str(payload.get("processDefinitionKey", "")),
                business_key=payload.get("businessKey"),
                is_ended=bool(payload.get("ended", False)),
                is_suspended=bool(payload.get("suspended", False)),
                start_time=parse_flowable_datetime(payload.get("startTime")),
                variables=flowable_variables_to_dict(payload.get("variables")),
            )

        if resp.status_code == 404:
            # Check history to see if instance completed
            hist_resp = await self._request(
                "GET",
                f"/history/historic-process-instances/{engine_instance_id}",
                params={"tenantId": tenant_id},
                is_idempotent=True,
            )
            if hist_resp.status_code == 200:
                h_payload = hist_resp.json()
                return EngineProcessInstance(
                    engine_instance_id=str(h_payload.get("id")),
                    engine_definition_id=str(h_payload.get("processDefinitionId", "")),
                    engine_definition_key=str(h_payload.get("processDefinitionKey", "")),
                    business_key=h_payload.get("businessKey"),
                    is_ended=True,
                    is_suspended=False,
                    start_time=parse_flowable_datetime(h_payload.get("startTime")),
                    end_time=parse_flowable_datetime(h_payload.get("endTime")),
                    variables=flowable_variables_to_dict(h_payload.get("variables")),
                )
            return None

        raise WorkflowEngineResponseError(
            f"Failed to fetch instance '{engine_instance_id}': status {resp.status_code}"
        )

    async def list_user_tasks(
        self,
        engine_instance_id: str | None = None,
        assignee: str | None = None,
        candidate_group: str | None = None,
        tenant_id: str | None = None,
    ) -> list[EngineTask]:
        """List active user tasks matching specified criteria."""
        params: dict[str, Any] = {}
        if engine_instance_id:
            params["processInstanceId"] = engine_instance_id
        if assignee:
            params["assignee"] = assignee
        if candidate_group:
            params["candidateGroup"] = candidate_group
        if tenant_id:
            params["tenantId"] = tenant_id

        resp = await self._request("GET", "/runtime/tasks", params=params, is_idempotent=True)
        if resp.status_code != 200:
            raise WorkflowEngineResponseError(
                f"Failed to query Flowable tasks: status {resp.status_code}: {resp.text}"
            )

        data = resp.json().get("data", [])
        tasks: list[EngineTask] = []
        for item in data:
            task_id = str(item.get("id"))
            # Fetch candidate groups / roles via identitylinks if candidate group not pre-populated
            candidate_roles: list[str] = []
            try:
                link_resp = await self._request(
                    "GET",
                    f"/runtime/tasks/{task_id}/identitylinks",
                    is_idempotent=True,
                )
                if link_resp.status_code == 200:
                    for link in link_resp.json():
                        if link.get("type") == "candidate" and link.get("group"):
                            candidate_roles.append(str(link.get("group")))
            except Exception:
                pass

            tasks.append(
                EngineTask(
                    engine_task_id=task_id,
                    task_definition_key=str(item.get("taskDefinitionKey", "")),
                    name=str(item.get("name", "")),
                    description=item.get("description"),
                    engine_instance_id=str(item.get("processInstanceId", "")),
                    assignee=item.get("assignee"),
                    candidate_roles=candidate_roles,
                    created_time=parse_flowable_datetime(item.get("createTime")),
                    due_date=parse_flowable_datetime(item.get("dueDate")),
                    variables=flowable_variables_to_dict(item.get("variables")),
                )
            )
        return tasks

    async def get_user_task(
        self,
        engine_task_id: str,
        tenant_id: str,
    ) -> EngineTask | None:
        """Retrieve a specific user task by engine task ID."""
        resp = await self._request(
            "GET",
            f"/runtime/tasks/{engine_task_id}",
            params={"tenantId": tenant_id},
            is_idempotent=True,
        )
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise WorkflowEngineResponseError(
                f"Failed to fetch task '{engine_task_id}': status {resp.status_code}"
            )

        item = resp.json()
        candidate_roles: list[str] = []
        try:
            link_resp = await self._request(
                "GET",
                f"/runtime/tasks/{engine_task_id}/identitylinks",
                is_idempotent=True,
            )
            if link_resp.status_code == 200:
                for link in link_resp.json():
                    if link.get("type") == "candidate" and link.get("group"):
                        candidate_roles.append(str(link.get("group")))
        except Exception:
            pass

        return EngineTask(
            engine_task_id=str(item.get("id")),
            task_definition_key=str(item.get("taskDefinitionKey", "")),
            name=str(item.get("name", "")),
            description=item.get("description"),
            engine_instance_id=str(item.get("processInstanceId", "")),
            assignee=item.get("assignee"),
            candidate_roles=candidate_roles,
            created_time=parse_flowable_datetime(item.get("createTime")),
            due_date=parse_flowable_datetime(item.get("dueDate")),
            variables=flowable_variables_to_dict(item.get("variables")),
        )

    async def complete_user_task(
        self,
        engine_task_id: str,
        action: str,
        variables: dict[str, Any] | None,
        tenant_id: str,
    ) -> EngineTaskCompletionResult:
        """Complete an active user task with outcome variables."""
        merged_vars = dict(variables or {})
        merged_vars["_task_action"] = action

        body = {
            "action": "complete",
            "variables": dict_to_flowable_variables(merged_vars),
        }
        resp = await self._request("POST", f"/runtime/tasks/{engine_task_id}", json=body)
        if resp.status_code == 404:
            raise WorkflowResourceNotFoundError(f"Engine task '{engine_task_id}' not found.")
        if resp.status_code not in (200, 204):
            code = resp.status_code
            msg = f"Failed to complete task '{engine_task_id}': status {code}: {resp.text}"
            raise WorkflowEngineResponseError(msg)

        return EngineTaskCompletionResult(
            engine_task_id=engine_task_id,
            status="COMPLETED",
            completed_at=datetime.now(UTC),
            variables=merged_vars,
        )

    async def claim_user_task(
        self,
        engine_task_id: str,
        assignee: str | None,
        tenant_id: str,
    ) -> None:
        """Claim or unclaim an active user task in Flowable."""
        body: dict[str, Any] = {"action": "claim"}
        if assignee is not None:
            body["assignee"] = assignee

        resp = await self._request("POST", f"/runtime/tasks/{engine_task_id}", json=body)
        if resp.status_code == 404:
            raise WorkflowResourceNotFoundError(f"Engine task '{engine_task_id}' not found.")
        if resp.status_code not in (200, 204):
            code = resp.status_code
            msg = f"Failed to claim task '{engine_task_id}': status {code}: {resp.text}"
            raise WorkflowEngineResponseError(msg)

    async def get_history(
        self,
        engine_instance_id: str,
        tenant_id: str,
    ) -> list[EngineHistoricActivity]:
        """Retrieve the normalized activity execution history for an instance."""
        resp = await self._request(
            "GET",
            "/history/historic-activity-instances",
            params={
                "processInstanceId": engine_instance_id,
                "tenantId": tenant_id,
                "sort": "startTime",
                "order": "asc",
                "size": 200,
            },
            is_idempotent=True,
        )
        if resp.status_code != 200:
            raise WorkflowEngineResponseError(
                f"Failed to query history for '{engine_instance_id}': status {resp.status_code}"
            )

        data = resp.json().get("data", [])
        activities: list[EngineHistoricActivity] = []
        for item in data:
            activities.append(
                EngineHistoricActivity(
                    activity_id=str(item.get("activityId", "")),
                    activity_name=item.get("activityName"),
                    activity_type=str(item.get("activityType", "")),
                    engine_instance_id=str(item.get("processInstanceId", "")),
                    execution_id=item.get("executionId"),
                    assignee=item.get("assignee"),
                    start_time=parse_flowable_datetime(item.get("startTime")),
                    end_time=parse_flowable_datetime(item.get("endTime")),
                    duration_ms=item.get("durationInMillis"),
                    is_completed=bool(item.get("endTime") is not None),
                )
            )
        return activities

    def _get_dmn_base_url(self) -> str:
        """Derive the Flowable DMN REST base URL from the BPMN base URL."""
        base = self.settings.base_url.rstrip("/")
        if base.endswith("/service"):
            return base[:-8] + "/dmn-api"
        return f"{base}/dmn-api"

    async def deploy_decision(
        self,
        dmn_xml: str,
        resource_name: str,
        tenant_id: str,
    ) -> EngineDeploymentResult:
        """Deploy a DMN 1.3 XML definition to the Flowable DMN engine."""
        ext = ".dmn"
        fname = resource_name if resource_name.endswith(ext) else f"{resource_name}{ext}"
        files = {
            "file": (
                fname,
                dmn_xml.encode("utf-8"),
                "application/xml",
            )
        }
        data = {"tenantId": tenant_id}
        url = f"{self._get_dmn_base_url()}/dmn-repository/deployments"
        resp = await self._request("POST", url, files=files, data=data)
        if resp.status_code not in (200, 201):
            raise WorkflowEngineResponseError(
                f"Failed to deploy DMN decision '{resource_name}': "
                f"status {resp.status_code}: {resp.text}"
            )
        body = resp.json()
        dep_id = str(body["id"])
        dep_time = parse_flowable_datetime(body.get("deploymentTime")) or datetime.now(UTC)
        return EngineDeploymentResult(
            deployment_id=dep_id,
            name=resource_name,
            engine_definition_id=dep_id,
            deployment_time=dep_time,
        )

    async def execute_decision(
        self,
        decision_key: str,
        variables: dict[str, Any],
        tenant_id: str,
    ) -> dict[str, Any]:
        """Execute a deployed DMN decision table deterministically against Flowable DMN."""
        input_vars = []
        for k, v in variables.items():
            if isinstance(v, bool):
                v_type = "boolean"
            elif isinstance(v, int):
                v_type = "integer"
            elif isinstance(v, float):
                v_type = "double"
            else:
                v_type = "string"
                v = str(v)
            input_vars.append({"name": str(k), "type": v_type, "value": v})

        payload = {
            "decisionKey": decision_key,
            "tenantId": tenant_id,
            "inputVariables": input_vars,
        }
        url = f"{self._get_dmn_base_url()}/dmn-rule/execute"
        resp = await self._request("POST", url, json=payload)
        if resp.status_code not in (200, 201):
            raise WorkflowEngineResponseError(
                f"Failed to execute DMN decision '{decision_key}': "
                f"status {resp.status_code}: {resp.text}"
            )
        body = resp.json()
        result: dict[str, Any] = {}
        raw_results = body.get("resultVariables")
        if isinstance(raw_results, list):
            for row in raw_results:
                if isinstance(row, list):
                    for var in row:
                        if isinstance(var, dict) and "name" in var:
                            result[var["name"]] = var.get("value")
                elif isinstance(row, dict) and "name" in row:
                    result[row["name"]] = row.get("value")
        elif isinstance(raw_results, dict):
            result = raw_results
        return result

    async def list_timer_jobs(
        self,
        process_instance_id: str | None = None,
        tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List active timer jobs from Flowable engine management API."""
        params: dict[str, Any] = {}
        if process_instance_id:
            params["processInstanceId"] = process_instance_id
        if tenant_id:
            params["tenantId"] = tenant_id

        resp = await self._request(
            "GET", "/management/timer-jobs", params=params, is_idempotent=True
        )
        if resp.status_code != 200:
            raise WorkflowEngineResponseError(
                f"Failed to list timer jobs: status {resp.status_code}: {resp.text}"
            )
        body = resp.json()
        return body.get("data", [])

    async def trigger_timer_job(self, job_id: str) -> None:
        """Trigger an active timer job by moving and executing it in Flowable."""
        try:
            resp = await self._request(
                "POST", f"/management/timer-jobs/{job_id}", json={"action": "move"}
            )
            if resp.status_code not in (200, 204):
                if resp.status_code == 404:
                    return
                raise WorkflowEngineResponseError(
                    f"Failed to move timer job '{job_id}': status {resp.status_code}: {resp.text}"
                )
        except Exception as exc:
            logger.info("Timer job %s move notice: %s", job_id, exc)

        try:
            exec_resp = await self._request(
                "POST", f"/management/jobs/{job_id}", json={"action": "execute"}
            )
            if exec_resp.status_code not in (200, 204, 404):
                if "OptimisticLockingException" in exec_resp.text:
                    logger.info("Timer job %s executed concurrently by Flowable engine", job_id)
                    return
                if exec_resp.status_code in (409, 500) and (
                    "not found" in exec_resp.text.lower() or "job" in exec_resp.text.lower()
                ):
                    return
                raise WorkflowEngineResponseError(
                    f"Failed to execute job '{job_id}': status {exec_resp.status_code}"
                )
        except Exception as exc:
            logger.info("Timer job %s execute notice: %s", job_id, exc)

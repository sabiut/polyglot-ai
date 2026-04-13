"""Kubernetes AI tools — let the AI query pods, deployments, services, and logs."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess

logger = logging.getLogger(__name__)


def _check_kubectl() -> bool:
    return shutil.which("kubectl") is not None


def _run_kubectl_sync(args: list[str], context: str = "", timeout: int = 10) -> tuple[str, int]:
    """Synchronous kubectl execution — called via ``asyncio.to_thread``."""
    if not _check_kubectl():
        return "Error: kubectl is not installed on this machine.", 1
    try:
        cmd = ["kubectl"]
        if context:
            cmd.extend(["--context", context])
        cmd.extend(args)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        output = result.stdout if result.returncode == 0 else (result.stderr or result.stdout)
        return output.strip(), result.returncode
    except subprocess.TimeoutExpired:
        return "Error: Command timed out", 1
    except Exception as exc:
        return f"Error: {exc}", 1


async def _run_kubectl(args: list[str], context: str = "", timeout: int = 10) -> tuple[str, int]:
    """Run kubectl without blocking the event loop."""
    return await asyncio.to_thread(_run_kubectl_sync, args, context, timeout)


def _ns_args(args: dict) -> list[str]:
    namespace = args.get("namespace", "")
    if namespace:
        return ["-n", namespace]
    return ["-A"]


async def k8s_current_context(args: dict) -> str:
    """Get the current kubectl context (active cluster)."""
    output, code = await _run_kubectl(["config", "current-context"])
    if code != 0:
        return f"Failed to get context: {output}"
    return f"Current context: {output}"


async def k8s_list_pods(args: dict) -> str:
    """List Kubernetes pods. Optional namespace filter."""
    context = args.get("context", "")
    output, code = await _run_kubectl(
        ["get", "pods", *_ns_args(args), "-o", "json"], context=context
    )
    if code != 0:
        return f"Failed to list pods: {output}"

    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return f"Failed to parse kubectl output: {output[:200]}"

    pods = data.get("items", [])
    if not pods:
        return "No pods found."

    lines = [f"Found {len(pods)} pod(s):\n"]
    for pod in pods:
        meta = pod.get("metadata", {})
        status = pod.get("status", {})
        phase = status.get("phase", "Unknown")
        container_statuses = status.get("containerStatuses", [])
        restarts = sum(cs.get("restartCount", 0) for cs in container_statuses)

        # Detailed status from container state
        detail = phase
        for cs in container_statuses:
            waiting = cs.get("waiting", {})
            if waiting.get("reason"):
                detail = waiting["reason"]
                break

        lines.append(
            f"- {meta.get('namespace', '?')}/{meta.get('name', '?')} "
            f"status={detail} "
            f"restarts={restarts}"
        )
    return "\n".join(lines)


async def k8s_list_deployments(args: dict) -> str:
    """List Kubernetes deployments with ready/desired replicas."""
    context = args.get("context", "")
    output, code = await _run_kubectl(
        ["get", "deployments", *_ns_args(args), "-o", "json"], context=context
    )
    if code != 0:
        return f"Failed to list deployments: {output}"

    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return f"Failed to parse kubectl output: {output[:200]}"

    deps = data.get("items", [])
    if not deps:
        return "No deployments found."

    lines = [f"Found {len(deps)} deployment(s):\n"]
    for dep in deps:
        meta = dep.get("metadata", {})
        spec = dep.get("spec", {})
        status = dep.get("status", {})
        ready = status.get("readyReplicas", 0)
        desired = spec.get("replicas", 0)
        lines.append(
            f"- {meta.get('namespace', '?')}/{meta.get('name', '?')} ready={ready}/{desired}"
        )
    return "\n".join(lines)


async def k8s_list_services(args: dict) -> str:
    """List Kubernetes services with type and ports."""
    context = args.get("context", "")
    output, code = await _run_kubectl(
        ["get", "services", *_ns_args(args), "-o", "json"], context=context
    )
    if code != 0:
        return f"Failed to list services: {output}"

    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return f"Failed to parse kubectl output: {output[:200]}"

    svcs = data.get("items", [])
    if not svcs:
        return "No services found."

    lines = [f"Found {len(svcs)} service(s):\n"]
    for svc in svcs:
        meta = svc.get("metadata", {})
        spec = svc.get("spec", {})
        svc_type = spec.get("type", "ClusterIP")
        ports = spec.get("ports", [])
        port_str = ", ".join(f"{p.get('port')}/{p.get('protocol', 'TCP')}" for p in ports)
        cluster_ip = spec.get("clusterIP", "")
        lines.append(
            f"- {meta.get('namespace', '?')}/{meta.get('name', '?')} "
            f"type={svc_type} ip={cluster_ip} ports=[{port_str}]"
        )
    return "\n".join(lines)


async def k8s_pod_logs(args: dict) -> str:
    """Get recent logs from a specific pod."""
    pod = args.get("pod", "") or args.get("name", "")
    namespace = args.get("namespace", "")
    context = args.get("context", "")
    tail = args.get("tail", 200)
    container = args.get("container", "")

    if not pod:
        return "Error: 'pod' name is required."
    if not namespace:
        return "Error: 'namespace' is required."

    if not isinstance(tail, int):
        try:
            tail = int(tail)
        except (ValueError, TypeError):
            tail = 200

    cmd = ["logs", pod, "-n", namespace, f"--tail={tail}"]
    if container:
        cmd.extend(["-c", container])
    else:
        cmd.append("--all-containers")

    output, code = await _run_kubectl(cmd, context=context, timeout=15)
    if code != 0:
        return f"Failed to get logs: {output}"
    if not output.strip():
        return f"Pod '{pod}' has no logs."
    if len(output) > 10_000:
        output = output[-10_000:]
        return f"[logs truncated, showing last 10000 chars]\n{output}"
    return output


async def k8s_delete_pod(args: dict) -> str:
    """Delete a pod (Kubernetes will recreate it if managed by a deployment)."""
    pod = args.get("pod", "") or args.get("name", "")
    namespace = args.get("namespace", "")
    context = args.get("context", "")
    if not pod or not namespace:
        return "Error: 'pod' and 'namespace' are required."
    output, code = await _run_kubectl(
        ["delete", "pod", pod, "-n", namespace], context=context, timeout=30
    )
    if code != 0:
        return f"Failed to delete pod: {output}"
    return f"Pod '{pod}' deleted. {output}"


async def k8s_restart_deployment(args: dict) -> str:
    """Trigger a rolling restart of a deployment."""
    deployment = args.get("deployment", "") or args.get("name", "")
    namespace = args.get("namespace", "")
    context = args.get("context", "")
    if not deployment or not namespace:
        return "Error: 'deployment' and 'namespace' are required."
    output, code = await _run_kubectl(
        ["rollout", "restart", f"deployment/{deployment}", "-n", namespace],
        context=context,
        timeout=30,
    )
    if code != 0:
        return f"Failed to restart deployment: {output}"
    return f"Deployment '{deployment}' restart triggered. {output}"


async def k8s_scale_deployment(args: dict) -> str:
    """Scale a deployment to a specific number of replicas."""
    deployment = args.get("deployment", "") or args.get("name", "")
    namespace = args.get("namespace", "")
    replicas = args.get("replicas", 1)
    context = args.get("context", "")
    if not deployment or not namespace:
        return "Error: 'deployment' and 'namespace' are required."
    if not isinstance(replicas, int):
        try:
            replicas = int(replicas)
        except (ValueError, TypeError):
            return "Error: 'replicas' must be an integer."
    output, code = await _run_kubectl(
        ["scale", f"deployment/{deployment}", f"--replicas={replicas}", "-n", namespace],
        context=context,
        timeout=30,
    )
    if code != 0:
        return f"Failed to scale deployment: {output}"
    return f"Deployment '{deployment}' scaled to {replicas} replicas. {output}"


async def k8s_apply(args: dict, *, project_root: "Path | None" = None) -> str:  # noqa: F821
    """Apply a Kubernetes manifest from YAML content or a file path.

    File paths are sandboxed: URLs are rejected, the path must resolve
    under the open project root, and the file must actually exist.
    This prevents the AI (or a compromised prompt) from asking kubectl
    to apply an arbitrary remote manifest (kubectl supports URLs via
    ``-f``) or a sensitive system path.

    ``project_root`` is passed by the tool dispatcher from the Sandbox.
    Falls back to ``Path.cwd()`` only when no project is open (which
    means the sandbox guard in ``_execute_single`` would have already
    rejected the call for non-standalone tools).
    """
    from pathlib import Path as _Path

    yaml_content = args.get("yaml", "") or args.get("manifest", "")
    file_path = args.get("file", "")
    context = args.get("context", "")

    if not yaml_content and not file_path:
        return "Error: either 'yaml' content or 'file' path is required."

    if file_path:
        # Reject URL schemes — kubectl supports them and they would bypass
        # any filesystem sandboxing.
        lowered = file_path.strip().lower()
        if "://" in lowered or lowered.startswith(("http:", "https:", "ftp:", "file:")):
            return (
                "Error: remote URLs are not allowed for k8s_apply. "
                "Download the manifest into the project first, then reference the local path."
            )
        try:
            root = (project_root or _Path.cwd()).resolve()
            resolved = _Path(file_path).expanduser().resolve()
        except (OSError, RuntimeError) as e:
            return f"Error resolving manifest path: {e}"

        try:
            resolved.relative_to(root)
        except ValueError:
            return (
                f"Error: manifest path '{resolved}' is outside the current project "
                f"('{root}'). Only files inside the open project may be applied."
            )

        if not resolved.is_file():
            return f"Error: manifest file not found: {resolved}"

        output, code = await _run_kubectl(
            ["apply", "-f", str(resolved)], context=context, timeout=30
        )
        if code != 0:
            return f"Failed to apply manifest: {output}"
        return f"Applied manifest from {resolved}:\n{output}"

    # Apply from stdin using yaml content
    try:
        cmd = ["kubectl"]
        if context:
            cmd.extend(["--context", context])
        cmd.extend(["apply", "-f", "-"])
        result = subprocess.run(
            cmd,
            input=yaml_content,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return f"Failed to apply manifest: {result.stderr}"
        return f"Applied manifest:\n{result.stdout}"
    except Exception as e:
        return f"Error applying manifest: {e}"


async def k8s_describe(args: dict) -> str:
    """Describe a Kubernetes resource (pod, deployment, service, etc.)."""
    resource_type = args.get("type", "") or args.get("resource", "")
    name = args.get("name", "")
    namespace = args.get("namespace", "")
    context = args.get("context", "")

    if not resource_type or not name:
        return "Error: 'type' and 'name' are required."
    if not namespace:
        return "Error: 'namespace' is required."

    output, code = await _run_kubectl(
        ["describe", resource_type, name, "-n", namespace],
        context=context,
        timeout=15,
    )
    if code != 0:
        return f"Failed to describe: {output}"
    if len(output) > 8000:
        output = output[:8000] + "\n... [truncated]"
    return output

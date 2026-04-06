"""Docker AI tools — let the AI query containers, images, and logs."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess

logger = logging.getLogger(__name__)


def _check_docker() -> bool:
    return shutil.which("docker") is not None


def _run_docker(args: list[str], timeout: int = 15) -> tuple[str, int]:
    if not _check_docker():
        return "Error: Docker is not installed on this machine.", 1
    try:
        result = subprocess.run(
            ["docker", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = result.stdout if result.returncode == 0 else (result.stderr or result.stdout)
        return output.strip(), result.returncode
    except subprocess.TimeoutExpired:
        return "Error: Command timed out", 1
    except Exception as exc:
        return f"Error: {exc}", 1


async def docker_list_containers(args: dict) -> str:
    """List Docker containers — running, stopped, or all."""
    show_all = args.get("all", True)
    filter_status = args.get("status", "").lower()

    cmd = ["ps", "--format", "{{json .}}"]
    if show_all:
        cmd.insert(1, "-a")

    output, code = _run_docker(cmd)
    if code != 0:
        return f"Failed to list containers: {output}"

    containers = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            if filter_status and filter_status not in data.get("State", "").lower():
                continue
            containers.append(data)
        except json.JSONDecodeError:
            continue

    if not containers:
        return "No containers found."

    lines = [f"Found {len(containers)} container(s):\n"]
    for c in containers:
        lines.append(
            f"- {c.get('Names', '?')} "
            f"({c.get('Image', '?')}) "
            f"state={c.get('State', '?')} "
            f"status={c.get('Status', '?')} "
            f"ports={c.get('Ports', '')}"
        )
    return "\n".join(lines)


async def docker_list_images(args: dict) -> str:
    """List Docker images on the local machine."""
    output, code = _run_docker(["images", "--format", "{{json .}}"])
    if code != 0:
        return f"Failed to list images: {output}"

    images = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            images.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if not images:
        return "No images found."

    lines = [f"Found {len(images)} image(s):\n"]
    for img in images:
        lines.append(
            f"- {img.get('Repository', '<none>')}:{img.get('Tag', '<none>')} "
            f"id={img.get('ID', '')[:12]} "
            f"size={img.get('Size', '')} "
            f"created={img.get('CreatedSince', '')}"
        )
    return "\n".join(lines)


async def docker_container_logs(args: dict) -> str:
    """Get recent logs from a Docker container."""
    name = args.get("container", "") or args.get("name", "")
    if not name:
        return "Error: 'container' name is required."
    tail = args.get("tail", 200)
    if not isinstance(tail, int):
        try:
            tail = int(tail)
        except (ValueError, TypeError):
            tail = 200

    if not _check_docker():
        return "Error: Docker is not installed."

    try:
        # docker logs writes to both stdout and stderr — capture both
        result = subprocess.run(
            ["docker", "logs", "--tail", str(tail), name],
            capture_output=True,
            text=True,
            timeout=15,
        )
        output = result.stdout + result.stderr
        if result.returncode != 0:
            return f"Failed to get logs for '{name}': {output[:500]}"
        if not output.strip():
            return f"Container '{name}' has no logs."
        # Truncate very long logs
        if len(output) > 10_000:
            output = output[-10_000:]
            return f"[logs truncated, showing last 10000 chars]\n{output}"
        return output
    except subprocess.TimeoutExpired:
        return "Error: Command timed out"
    except Exception as exc:
        return f"Error: {exc}"


async def docker_restart(args: dict) -> str:
    """Restart a Docker container."""
    name = args.get("container", "") or args.get("name", "")
    if not name:
        return "Error: 'container' name is required."
    output, code = _run_docker(["restart", name], timeout=30)
    if code != 0:
        return f"Failed to restart '{name}': {output}"
    return f"Container '{name}' restarted successfully."


async def docker_stop(args: dict) -> str:
    """Stop a running Docker container."""
    name = args.get("container", "") or args.get("name", "")
    if not name:
        return "Error: 'container' name is required."
    output, code = _run_docker(["stop", name], timeout=30)
    if code != 0:
        return f"Failed to stop '{name}': {output}"
    return f"Container '{name}' stopped successfully."


async def docker_start(args: dict) -> str:
    """Start a stopped Docker container."""
    name = args.get("container", "") or args.get("name", "")
    if not name:
        return "Error: 'container' name is required."
    output, code = _run_docker(["start", name], timeout=30)
    if code != 0:
        return f"Failed to start '{name}': {output}"
    return f"Container '{name}' started successfully."


async def docker_remove(args: dict) -> str:
    """Remove a Docker container (must be stopped) or image."""
    name = args.get("container", "") or args.get("name", "") or args.get("image", "")
    force = args.get("force", False)
    is_image = args.get("is_image", False)
    if not name:
        return "Error: 'name' is required."
    cmd = ["rmi" if is_image else "rm"]
    if force:
        cmd.append("-f")
    cmd.append(name)
    output, code = _run_docker(cmd)
    if code != 0:
        return f"Failed to remove '{name}': {output}"
    return f"{'Image' if is_image else 'Container'} '{name}' removed successfully."


async def docker_inspect(args: dict) -> str:
    """Inspect a Docker container or image for detailed info."""
    name = args.get("name", "") or args.get("container", "")
    if not name:
        return "Error: 'name' is required."

    output, code = _run_docker(["inspect", name])
    if code != 0:
        return f"Failed to inspect '{name}': {output}"

    try:
        data = json.loads(output)
        # Return compact summary instead of full dump
        if isinstance(data, list) and data:
            item = data[0]
            summary = {
                "Id": item.get("Id", "")[:12],
                "Name": item.get("Name", "").lstrip("/"),
                "Image": item.get("Config", {}).get("Image", ""),
                "State": item.get("State", {}),
                "NetworkSettings": {
                    "IPAddress": item.get("NetworkSettings", {}).get("IPAddress", ""),
                    "Ports": item.get("NetworkSettings", {}).get("Ports", {}),
                },
                "Mounts": item.get("Mounts", []),
                "Env": item.get("Config", {}).get("Env", [])[:20],
                "Cmd": item.get("Config", {}).get("Cmd", []),
            }
            return json.dumps(summary, indent=2, default=str)
    except json.JSONDecodeError:
        pass

    return output[:5000]

# CI / Docker / Kubernetes

Three panels that cover the infrastructure side of day-to-day dev work.

---

## CI/CD

Open with `Ctrl+Shift+I`. The CI panel reads workflow runs via `gh` (GitHub
Actions) and displays them in a filterable table.

### Runs table

Columns: workflow, branch, status, duration, triggered by, when. Status is
colour-coded:

- **Success** ✓ — green
- **Failure** ✗ — red
- **In progress** … — orange
- **Queued** — grey

### Filters

- **Branch** — if a task is active with a branch, the table auto-filters to that branch.
- **Status** — only failures, only in-progress, etc.
- **Workflow** — filter by workflow file name.

### Run detail

Click a run to see:

- Job list with status per job.
- Log output per step (streamed as the run progresses).
- Failure annotations extracted from logs.

### Actions

- **Re-run** — re-runs a failed run via `gh run rerun`.
- **Re-run failed jobs only** — `gh run rerun --failed`.
- **Cancel** — cancels an in-progress run.
- **Open in browser** — opens the run on github.com.
- **Import failure as incident** — creates a new INCIDENT task seeded with
  the workflow name, status, and run URL. The task is made active so the
  rest of the app re-scopes to it.

### Task integration

Every status change on the task's branch writes a `CIRunSnapshot` to the
task (`status`, `workflow`, `url`, `timestamp`) and appends a `ci_run`
note.

### Requirements

- `gh` CLI installed and authenticated (`gh auth login`).

---

## Docker

The Docker panel provides a lightweight view of your local Docker engine.

### Views

- **Containers** — running and stopped. Columns: name, image, status, ports, created.
- **Images** — pulled images. Columns: repo, tag, size, created.
- **Volumes** — named volumes.
- **Networks** — user-defined networks.

### Actions per container

- Start / stop / restart / remove.
- **Logs** — streamed in a viewer tab.
- **Exec** — opens an interactive shell in the container (uses the integrated terminal).
- **Inspect** — full JSON from `docker inspect`.

### Actions per image

- Remove, prune dangling, pull an update.

### Requirements

- Docker engine running.
- Current user has permission to talk to the Docker socket.

---

## Kubernetes

Open with `Ctrl+Shift+8`. The k8s panel connects to whatever context is
selected in your current kubeconfig.

### Views

- **Contexts / namespaces** — switch context and namespace at the top.
- **Workloads** — deployments, statefulsets, daemonsets, jobs, cronjobs.
- **Pods** — pods in the current namespace with status, node, age, restarts.
- **Services** — services with type, cluster IP, ports.
- **ConfigMaps / Secrets** — listed; secrets show key names only (never values).

### Per-pod actions

- **Logs** — streamed in a viewer tab. Supports follow mode and log level colouring.
- **Exec** — `kubectl exec -it` into the pod via the integrated terminal.
- **Describe** — runs `kubectl describe` and shows the output.
- **Port-forward** — forwards a container port to localhost.
- **Delete** — with a confirmation dialog.

### Apply and diff

- **Apply YAML** — paste YAML, it runs `kubectl apply`.
- **Dry-run + diff** — runs `kubectl diff` against the cluster before
  applying so you can see exactly what would change. Strongly recommended
  before any apply on a shared cluster.

### Requirements

- `kubectl` installed and on `PATH`.
- A valid kubeconfig with at least one context.

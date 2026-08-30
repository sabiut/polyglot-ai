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

### Run detail

Click a run to see:

- Job list with status and duration per job.
- **View logs** — streams the logs of the failed jobs via `gh` into a
  viewer (a Cancel button aborts the in-flight log fetch).
- **Open in browser** — opens the run on github.com.

### Actions

Right-click a run for the context menu:

- **View jobs / logs** — same as clicking the run.
- **Debug this failure as a new task** (failed/cancelled runs only) —
  creates a new INCIDENT task seeded with the workflow name, branch,
  status, and run id, and binds the failing branch to it.

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

- **Containers** — running and stopped. Columns: name, image, status, ports.
- **Images** — pulled images. Columns: repository, tag, size.

### Actions per container (right-click)

- **Start** / **Stop** / **Restart** (with a confirmation dialog).
- **View Logs** — shown in a viewer.

### Actions per image (right-click)

- **Copy Image Name**.
- **Inspect** — output from `docker inspect`.
- **Run Container**.
- **Delete Image**.

### Requirements

- Docker engine running.
- Current user has permission to talk to the Docker socket.

---

## Kubernetes

Open with `Ctrl+Shift+8`. The k8s panel connects to whatever context is
selected in your current kubeconfig.

### Views

- **Contexts / namespaces** — switch context and namespace at the top.
- **Pods** — pods in the current namespace.
- **Deployments** — deployments in the current namespace.
- **Services** — services in the current namespace.

Other resource kinds (statefulsets, jobs, configmaps, secrets, …) aren't
browsable yet.

### Per-pod actions (right-click)

- **View Logs** — shown in a viewer.
- **Describe** — runs `kubectl describe` and shows the output.
- **Delete Pod** — with a confirmation dialog.

### Per-deployment actions (right-click)

- **Describe**.
- **Restart Rollout**.
- **Scale**.

### Per-service actions (right-click)

- **Describe**.

### Requirements

- `kubectl` installed and on `PATH`.
- A valid kubeconfig with at least one context.

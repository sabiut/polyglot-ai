# CI / Docker / Kubernetes / AWS

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

## AWS

The AWS panel (activity bar cloud icon, `Ctrl+Shift+W`) shows what's
running in an account at a glance, using your own `aws` CLI — so
profiles, SSO sessions, `aws-vault` and MFA work exactly as they do in
a terminal, and Polyglot never reads your credential files.

**Requirements:** the [AWS CLI](https://aws.amazon.com/cli/) installed
and at least one profile configured (`aws configure` or `aws sso login`).

### What it shows

Pick a **profile** and **region** at the top (the region defaults to the
profile's configured one). The panel then lists, in one refresh:

- **Lambda** functions — runtime and last-modified time
- **EC2** instances — Name tag, state (colour-coded), type, IP
- **ECS services** — cluster/service, running/desired tasks
- **S3 buckets** — creation date (buckets are global; the list doesn't
  change with region)

The account ID and caller identity appear under the selectors. Each
section fails independently: if a profile lacks permission for one
service you see a hint under that section and the rest still loads.

Click a resource to load its details below: a Lambda's configuration,
an instance description, a service description, or a bucket's top-level
listing. There is no auto-refresh — every call is a real API request —
so use the refresh button when you want fresh data.

### Actions (right-click)

- **Lambda** — *Tail logs (last hour)* from CloudWatch, *Configuration*,
  *Invoke…* with a JSON payload (asks for confirmation)
- **EC2** — *Describe*; *Stop* / *Reboot* a running instance or *Start*
  a stopped one (each asks for confirmation)
- **ECS** — *Describe*, *Recent events*, *Force new deployment…*
- **S3** — *List top-level objects*, *Copy s3:// URI*

Every resource also has **Send details to AI**, which puts the details
pane into the chat with a prompt to explain it and suggest next steps —
the fastest route from "this Lambda is failing" to a fix. The chat
button in the details header does the same.

### The AI can use AWS too

Two tools are available to the assistant when you chat:

- `aws_cli` — runs any AWS CLI command with your profile. **Read-only
  calls** (`describe-*`, `list-*`, `get-*`, `s3 ls`, `logs tail`, …)
  run immediately. Anything that changes state shows an approval card
  with the exact command first; destructive verbs (`terminate`,
  `delete`, `s3 rm`, bucket-policy changes, …) get an amber warning.
- `aws_logs_tail` — fetches recent CloudWatch Logs from a log group,
  optionally filtered (e.g. `ERROR`).

So you can ask things like *"which of my EC2 instances have been running
for more than a month?"* or *"why is api-handler throwing errors — check
its logs"* and the assistant will look, read-only, without a prompt.


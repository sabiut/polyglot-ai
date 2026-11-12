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
profile's configured one). One refresh lists, in parallel:

| Group | Resources | Shown in the list |
|---|---|---|
| Compute | **EC2** instances | Name, state (coloured), type, IP |
| | **Lambda** functions | runtime, last modified |
| | **ECS services** | cluster / service, running/desired tasks |
| | **EKS clusters** | status, Kubernetes version |
| Data | **S3 buckets** | the bucket's region, creation date (all regions are listed) |
| | **RDS databases** | status (coloured), engine and class |
| | **DynamoDB tables** | status, item count |
| Messaging | **SQS queues** | messages waiting and in flight |
| | **SNS topics** | confirmed subscribers |
| Operations | **CloudFormation stacks** | status (failures and rollbacks in red), last update |
| | **CloudWatch alarms** | state — firing alarms listed first |

Sections with nothing in them fold into one *None here: …* line, so the
list only shows what you have. Click a section's ▾ / ▸ header to collapse
or expand it; collapsed sections stay collapsed across refreshes. Type in
**Filter resources…** to narrow every section at once.

The status and count for EKS, DynamoDB, SQS and SNS take one extra
request per row, so they are filled in for the first twelve rows of each
section. A section that was cut short says so — *EC2 (200+)*.

The account ID and caller identity appear under the selectors. Each
section fails independently: if a profile lacks permission for one
service you see a hint under that section, naming the action that was
refused, and the rest still loads. Switching profile or region clears
the list and the details at once, so nothing on screen belongs to the
previous account.

**Click a resource to see its details** in the pane below: a readable
summary first (for example an instance's state, IPs, VPC and security
groups; a database's engine, endpoint, storage and backups; an alarm's
state and why; a bucket's region, public-access block, encryption and
versioning), then the full CLI output under *Full output*. Lambda
summaries list environment variable *names* only. The header buttons
open the resource in the **AWS Console** in your browser, send the
details to the AI, or open them in a larger window. There is no
auto-refresh — every call is a real API request — so use the refresh
button when you want fresh data.

### Actions (right-click)

Every resource has **Open in AWS Console**, **Copy ID / ARN / URL** and
**Send details to AI**. On top of that:

- **Lambda** — *Tail logs (last hour)* from CloudWatch, *Configuration*,
  *Invoke…* with a JSON payload (asks for confirmation)
- **EC2** — *Describe*; *Connect via SSH…* (opens the SSH dialog
  pre-filled with the instance address) and *Connect via Session
  Manager (SSM)* (runs `aws ssm start-session` in the terminal — no
  open port 22 needed) for a running instance; *Stop* / *Reboot* a
  running instance or *Start* a stopped one (each asks for confirmation)
- **ECS** — *Describe*, *Recent events*, *Running tasks*, *Force new
  deployment…*
- **EKS** — *Describe*, *Node groups*, *Add to kubeconfig…* (runs
  `aws eks update-kubeconfig`, after asking, so the Kubernetes panel can
  browse the cluster)
- **S3** — *List top-level objects*, *Copy s3:// URI*
- **RDS** — *Describe*, *Connect in Database panel…* (opens the
  [Database panel](Database#connecting-to-an-rds-database-from-the-aws-panel)'s
  connection dialog with the endpoint, engine and user filled in, and a
  Session Manager route if the database is private); *Snapshots* (lists
  the manual and automated snapshots that exist); *Create snapshot…*
  (from 0.18.22 — see below)

**Create snapshot…** takes a manual RDS snapshot: AWS's own complete,
restorable copy of the instance. It asks for a name (suggesting
`<instance>-manual-<date>-<time>`), checks it against RDS's naming
rules, then shows a confirmation with the profile, region and the exact
`aws rds create-db-snapshot …` command before running anything. For an
Aurora instance, or a member of a Multi-AZ DB cluster, the snapshot is
taken of the **cluster** (`create-db-cluster-snapshot`), because RDS
doesn't snapshot those instance by instance — the confirmation says so.
A manual snapshot is kept, and billed as backup storage, until you
delete it; on a Single-AZ instance, reads and writes can pause for a
moment while it starts. Restoring a snapshot creates a *new* instance
and is done from the AWS console or CLI. To get the data as a file
instead, use [Back up in the Database panel](Database#backing-up-and-restoring-from-01822).

**Send details to AI** puts the details pane into the chat with a prompt
to explain it and suggest next steps — the fastest route from "this
Lambda is failing" to a fix. Secrets in the details are redacted before
they're sent.

### The AI can use AWS too

Two tools are available to the assistant when you chat:

- `aws_cli` — runs any AWS CLI command. **Read-only calls**
  (`describe-*`, `list-*`, `get-*`, `s3 ls`, `logs tail`, …) run
  immediately. Anything that changes state shows an approval card with
  the exact command first; destructive verbs (`terminate`, `delete`,
  `s3 rm`, bucket-policy changes, …) get an amber warning.
- `aws_logs_tail` — fetches recent CloudWatch Logs from a log group,
  optionally filtered (e.g. `ERROR`).

Both use the **profile and region selected in the AWS panel** unless you
name others in your message, and the approval card shows them — so you
can see which account a command is about to run against. What runs is
what the card showed, even if you change the selection while it waits.

Some calls that look like reads still ask first, because of what they
return or touch:

- stored data — `query`, `scan`, `select` and fetching an item, record
  or result;
- credentials and secrets — tokens, passwords, API keys, pre-signed
  links, instance user data;
- anything with a `file://` or `fileb://` argument, or that writes its
  result to a local file.

So you can ask things like *"which of my EC2 instances have been running
for more than a month?"* or *"why is api-handler throwing errors — check
its logs"* and the assistant will look, read-only, without a prompt.


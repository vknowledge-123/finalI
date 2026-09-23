# Part Eight Extended Google Cloud and Kubernetes Labs

## Chapter 47 Networking from addresses to application requests

### The mental model

A network address identifies an interface within a routing context. A port identifies an application endpoint on that address. `127.0.0.1:8015` is loopback on the machine where the command runs, not automatically your VM. A browser on your laptop reaches the VM only through a routable endpoint, an authenticated tunnel, or a proxy.

TCP establishes a reliable byte stream; it does not understand whether bytes represent a valid order. TLS protects the connection and verifies the server identity when configured correctly. HTTP adds request semantics. WebSocket upgrades an HTTP handshake into a bidirectional message channel. Each layer can succeed while a higher layer fails.

Use CIDR notation to describe a network prefix. For `10.42.0.0/24`, 24 bits are the network prefix. Cloud providers reserve some addresses in each subnet, so do not assume every mathematically available address can be assigned to a VM.

```python
from ipaddress import ip_network, ip_address

subnet = ip_network("10.42.0.0/24")
assert ip_address("10.42.0.8") in subnet
assert ip_address("10.43.0.8") not in subnet
assert subnet.num_addresses == 256
```

### Trace a private database connection

Suppose the API has a private address and Cloud SQL is reached privately. You need identity permission, a valid database credential or IAM database identity, a reachable network path, compatible connection settings, and database authorization. The Cloud SQL Auth Proxy authenticates and encrypts its connection but does not magically create missing private network reachability. Distinguish IAM permission to connect from SQL permission to select a table.

### Diagnose in layers

Start with the exact destination and port. Resolve DNS. Inspect the listening process. Check route and firewall policy. Check TLS hostnames. Then inspect application authentication and response data. Do not disable every firewall rule to determine whether a login password is wrong.

```bash
# On your learning VM, read-only diagnostics:
hostname
ip address
ip route
ss -ltn
curl --max-time 5 -i http://127.0.0.1:8015/health
```

Expected response from the paper lab contains `PAPER_ONLY`. A timeout, connection refusal, 401, and 502 are different evidence. Write down the observed category before changing configuration.

### Exercise and interview checkpoint

Draw laptop, IAP tunnel, VM, Nginx, API, and database. Label private and public addresses, ports, TLS termination, and identity checks. Explain how a request reaches a private VM without exposing SSH globally. Explain why allowing ingress port 443 does not grant access to the broker account.

## Chapter 48 Containerize the paper application

### Prerequisites and scope

Install Docker using its official platform instructions, use Linux containers, and confirm `docker version` shows both client and server. On Windows, the local runtime may use WSL2. Do not mix Windows container images with the Linux example. Docker is not installed in the authoring environment, so the following build is a lab you must run; it is not reported as already executed.

Build from `docs/learning-book/lab`. The Dockerfile copies only the learning package and runtime dependencies. It does not copy the production app or its credentials.

{{include:lab/Dockerfile|dockerfile}}

### Read the Dockerfile line by line

The base image supplies Python and Linux userspace. `WORKDIR` sets the process working directory. Copying the small dependency file before application source allows unchanged dependencies to reuse a cached build layer. A non-root user limits some consequences of compromise. `EXPOSE` is metadata; it does not publish a port by itself.

The JSON-array CMD launches Python directly, improving signal delivery compared with hiding the process behind an unnecessary shell. Binding Uvicorn to `0.0.0.0` inside the container makes it reachable through container networking. The host publishing rule below still binds only to laptop loopback.

```bash
docker build -t paper-school:lesson1 .
docker run --rm --name paper-school-local \
  -p 127.0.0.1:8015:8015 paper-school:lesson1
```

Open `http://127.0.0.1:8015`, save a strategy, and send a synthetic signal as described in the lab README. In another terminal:

```bash
docker logs --tail 50 paper-school-local
docker stats --no-stream paper-school-local
docker inspect paper-school-local --format '{{.Config.User}}'
```

Expected configured user is `10001:10001`. The database currently lives in the container's writable storage. Removing this container removes that synthetic data. Do not interpret this as a production persistence design.

### Reproducibility and security

The image tag `python:3.12-slim` can move. For a release, record and review a digest, rebuild for security updates, scan dependencies, and run tests. The teaching dependency versions mirror the small lab; they are not a certification of current production security. Never COPY `.env` into an image and then delete it in a later layer, because earlier layers can retain the content.

### Exercise

Stop the local container with `docker stop paper-school-local`. Run it again and check which records remain. Then mount a named volume at `/data`, create another record, and repeat. Explain the difference between process lifetime, container filesystem lifetime, and named-volume lifetime. Remove only the named teaching volume after confirming the synthetic data is disposable.

## Chapter 49 What Kubernetes actually controls

Kubernetes is a desired-state control system. You submit objects to an API server. Controllers compare desired and observed state and take action. The scheduler chooses suitable nodes for unscheduled Pods; the kubelet manages containers on a node. Cluster state is maintained through the control plane. Kubernetes does not understand whether a broker order is duplicated or whether a strategy's target was reached. [Kubernetes components](https://kubernetes.io/docs/concepts/overview/components/)

```text
kubectl -> API server -> persisted object state
                         |
                 controllers observe difference
                         |
                 scheduler selects a node
                         |
                 kubelet starts container
                         |
                 status returns to API server
```

### Objects and reconciliation

A manifest contains `apiVersion`, `kind`, `metadata`, and usually `spec`. Status is observed state; do not put a fabricated healthy status into a manifest. Labels are key-value selectors used to associate objects. An annotation carries metadata that is not intended as a selector.

Namespaces organize names and policy scope. They are not complete security isolation by themselves. Two client namespaces still require network controls, RBAC, resource limits, and data/secret separation. If administrators share unrestricted cluster access, they may see both clients.

### Failure thought experiment

When a container exits, the Pod's restart policy may restart it. When a Pod disappears, its controlling workload may create a replacement. When a node fails, recovery depends on the cluster and storage design. None of these actions repairs a corrupt ledger or determines whether an order was accepted before the crash.

**Exercise:** Write two columns: orchestration responsibility and application responsibility. Put replacing a failed API Pod in the first, and reconciling an unknown order in the second. Place database failover and secret rotation carefully: both involve infrastructure and application behavior.

## Chapter 50 Your first local cluster with kind

Use kind to run a local Kubernetes learning cluster in containers. Install a supported Docker runtime, kind, and kubectl from their official instructions, then confirm version compatibility. The [kind quick start](https://kind.sigs.k8s.io/docs/user/quick-start/) documents cluster creation and loading local images. Do not use this cluster to hold real broker credentials.

### Create and inspect

From the paper lab directory, build the image first, then run:

```bash
kind create cluster --name paper-school
kubectl --context kind-paper-school cluster-info
kubectl --context kind-paper-school get nodes
kind load docker-image paper-school:lesson1 --name paper-school
kubectl --context kind-paper-school apply -f k8s/namespace.yaml
```

Using `--context` explicitly reduces accidental changes to a client cluster. Always inspect `kubectl config current-context` before destructive operations. Your kubeconfig can contain access credentials; treat it as sensitive.

The namespace manifest is:

{{include:lab/k8s/namespace.yaml|yaml}}

### Apply and understand the output

`created` means the API accepted a new object. `configured` means an existing object was updated. Neither means the image pulled, container started, or application is ready. Use workload status and events for those questions.

```bash
kubectl --context kind-paper-school apply -f k8s/paper.yaml
kubectl --context kind-paper-school -n paper-school get pods -o wide
kubectl --context kind-paper-school -n paper-school get events \
  --sort-by=.metadata.creationTimestamp
```

### Cleanup boundary

When the exercises are finished and synthetic data is no longer needed, `kind delete cluster --name paper-school` removes that named local cluster. It does not remove every Docker image on your computer. Do not use broad Docker prune commands against a machine that also hosts unrelated work.

**Exercise:** Intentionally use an image tag that does not exist. Inspect events and describe the difference between object creation and successful execution. Repair the image reference, then wait for rollout readiness.

## Chapter 51 Deployments labels and a complete paper manifest

A Deployment manages a changing set of Pods through ReplicaSets. Its selector must match its Pod-template labels. A Service uses its own selector to find Pods. A typo between those labels can produce a healthy-looking Pod with no reachable Service endpoints. [Deployment reference](https://kubernetes.io/docs/concepts/workloads/controllers/deployment/)

The supplied lab deliberately uses one replica and `Recreate`. It stores a disposable SQLite file in `emptyDir`. This avoids presenting multiple uncoordinated SQLite instances as a scalable system. Pod replacement loses the synthetic database. Production durability is a later design, not a hidden property of this example.

{{include:lab/k8s/paper.yaml|yaml}}

### Understand each group

The ServiceAccount names the Kubernetes workload identity and disables automatic API-token mounting because the lab does not call Kubernetes. The ConfigMap supplies a nonsecret database path. The Pod security context uses non-root UID and group values, an appropriate seccomp profile, and a group for mounted-volume access. The container drops capabilities and uses a read-only image filesystem; `/data` and `/tmp` remain writable mounts.

Requests describe scheduling needs. Limits constrain usage. The probe paths refer to the lab's actual `/health` endpoint. The Service remains `ClusterIP`, so no public load balancer is created for an unauthenticated learning application.

### Verification

```bash
kubectl --context kind-paper-school -n paper-school rollout status \
  deployment/paper-api --timeout=180s
kubectl --context kind-paper-school -n paper-school get deployment,replicaset,pod,service
kubectl --context kind-paper-school -n paper-school logs deployment/paper-api --tail=60
```

**Exercise:** Change a harmless annotation on the Deployment itself, then on the Pod template, and observe which causes replacement. Predict the result before running. Do not scale this SQLite exercise above one replica; first implement an external shared ledger and test concurrency.

## Chapter 52 Resource requests limits and health probes

A request helps the scheduler place a workload. A CPU limit can cause throttling; a memory limit can lead to termination when exceeded. Increasing a memory limit does not repair an unbounded queue. Record actual working set, event-loop lag, and peak load before choosing values.

In the lab, `250m` means one quarter of a CPU unit requested. `256Mi` is a memory request, distinct from decimal megabytes. The example values are starting points for measurement, not capacity guarantees for the real trading app. Autopilot or admission policy may adjust resource settings; inspect the admitted Pod.

### Three probe questions

Startup asks whether initialization has completed. Readiness asks whether traffic should be sent to this Pod. Liveness asks whether restarting the process is warranted. A startup probe can protect slow initialization from premature liveness failure. Failed readiness removes normal Service traffic eligibility but does not by itself restart the process. [Probe documentation](https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/)

The small lab uses `/health` for all three because it has no live provider dependency. A production rebuild should distinguish them. A disconnected broker should not automatically cause the dashboard process to restart, and a database outage should not cause every Pod to enter a synchronized restart loop.

### Commands and interpretation

```bash
kubectl -n paper-school describe pod POD_NAME
kubectl -n paper-school logs POD_NAME --previous
kubectl -n paper-school top pods
```

Replace `POD_NAME` with an actual name and verify your context first. `--previous` inspects the previous container instance after a restart. `top` requires a working metrics pipeline, which a basic local cluster may not include. Missing metrics is not the same as zero usage.

**Exercise:** Use a separate test deployment with a wrong readiness path and observe Running but not Ready. Then repair it. Inject slow startup and tune startup-probe allowance. Document why liveness should not make a fresh broker order call on every probe.

## Chapter 53 Services DNS and private browser access

Pod addresses change. A Service provides a stable discovery abstraction for matching workloads. ClusterIP is internal; NodePort and LoadBalancer expose different paths with different infrastructure implications. A Service does not create application authentication. [Service documentation](https://kubernetes.io/docs/concepts/services-networking/service/)

### Open the paper lab safely

```bash
kubectl --context kind-paper-school -n paper-school port-forward \
  service/paper-api 8015:80
```

Open `http://127.0.0.1:8015`. Keep the port-forward process running while using the browser. Ctrl+C stops the tunnel, not the Pod. Port-forward is an operator debugging path and is not a test of ordinary Service load-balancing behavior or network-policy enforcement.

Inspect the Service and EndpointSlices:

```bash
kubectl --context kind-paper-school -n paper-school get service paper-api -o yaml
kubectl --context kind-paper-school -n paper-school get endpointslices \
  -l kubernetes.io/service-name=paper-api
```

No ready endpoint can indicate selector mismatch or readiness failure. A DNS name resolving to a Service address does not prove there is a ready backend. Inside the namespace, `paper-api` is a useful short service name; cross-namespace access requires the intended namespace-qualified name under cluster DNS conventions.

### Public traffic is a later exercise

Ingress and Gateway resources require compatible controllers. Merely applying an Ingress object does not install a controller or a certificate. Before exposing your rebuild, add tested authentication, authorization, HTTPS, request limits, session policy, and WebSocket support for the chosen controller. Do not expose the supplied unauthenticated lab through LoadBalancer.

**Exercise:** In an isolated cluster, change the Service selector to a nonexistent label. Observe Service existence with no matching backends. Restore it and explain why restarting the application was unnecessary.

## Chapter 54 Configuration secrets RBAC and network policy

Use ConfigMaps for nonsecret settings. Use a controlled secret mechanism for credentials. Kubernetes Secret data commonly appears base64-encoded in manifests; encoding is not encryption. Restrict RBAC, enable appropriate at-rest protection, and avoid committing secret manifests to Git. [Kubernetes Secret practices](https://kubernetes.io/docs/concepts/security/secrets-good-practices/)

Environment variables loaded at container start do not automatically update when a ConfigMap changes. Mounted configuration has different update behavior, but applications must still reload it safely. Broker-token rotation needs a versioned, explicit handover; it is not solved by editing one Kubernetes object.

### Minimal RBAC lesson

The paper API does not need Kubernetes API permissions. For a separate observer service, a namespaced Role can permit only reading Pod status:

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: pod-observer
  namespace: paper-school
rules:
  - apiGroups: [""]
    resources: [pods]
    verbs: [get, list, watch]
```

A RoleBinding must attach it to the intended identity before it grants access. Do not bind it to every service account. `kubectl auth can-i` can help test permissions, but impersonation checks require privileges of their own. Reading Secrets is a different permission; do not grant it to a log viewer by habit.

### NetworkPolicy lesson

{{include:lab/k8s/network-policy.yaml|yaml}}

This selects the paper API, allows ingress only from labeled test-client Pods in the same namespace on 8015, and allows no egress. It is optional and requires a network implementation that actually enforces policy. Default kind networking may not. The paper app has no external dependency, so denying egress is an intentional lesson. A real broker worker would need carefully designed DNS and broker/Google API egress. [NetworkPolicy semantics](https://kubernetes.io/docs/concepts/services-networking/network-policies/)

**Exercise:** Use a policy-enforcing cluster to compare two test Pods, one carrying the allowed label and one without it. Verify allowed and denied communication through the normal Service path, not just port-forward. Record the CNI and policy behavior used by the test.

## Chapter 55 Persistent storage and database placement

`emptyDir` belongs to a Pod's lifetime. Container restarts within that Pod can preserve it, while Pod replacement removes it. A persistent volume claim asks for storage independent of one Pod. Its storage class, access modes, reclaim policy, and topology affect actual behavior. A PVC is not a backup. [Persistent volumes](https://kubernetes.io/docs/concepts/storage/persistent-volumes/)

### A teaching claim

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: paper-data
  namespace: paper-school
spec:
  accessModes: [ReadWriteOnce]
  resources:
    requests:
      storage: 1Gi
```

This assumes a default storage class and compatible provisioner; otherwise specify a supported class or expect Pending. To use it, replace only the `data` volume's `emptyDir` with `persistentVolumeClaim: {claimName: paper-data}` in a separate teaching manifest. Keep one replica. ReadWriteOnce generally describes node attachment access, not an application-level single-writer guarantee across every possible Pod arrangement.

### Why not put every database in Kubernetes immediately

A StatefulSet provides stable identities and storage relationships; it does not implement database replication, backup consistency, schema migration, or failover correctness for you. Self-managed PostgreSQL and Redis require operational expertise. A managed database may be preferable when its cost and constraints fit.

For the next rebuild stage, place the authoritative ledger in PostgreSQL and keep API Pods stateless with bounded connection pools. Let Redis hold cache and coordination data under a defined persistence policy. Keep schema migration as a controlled deployment step, not a race executed independently by every Pod at startup.

**Exercise:** Create a paper record, restart a container, replace a Pod, and compare outcomes with emptyDir and with a PVC. Back up the database, delete a test record, restore into an isolated instance, and verify totals. Explain the difference between persistence, replication, and recovery.

## Chapter 56 Rollouts autoscaling jobs and safe ownership

Stateless HTTP services can often use rolling replacement. Your execution owner is different: overlapping old and new workers can be unsafe without account ownership and reconciliation. The paper SQLite lab uses Recreate and remains single-replica. Even Recreate is not a substitute for an application ownership protocol in the real system.

```bash
kubectl -n paper-school rollout history deployment/paper-api
kubectl -n paper-school rollout status deployment/paper-api --timeout=180s
kubectl -n paper-school rollout undo deployment/paper-api
```

Use these only after checking context and whether the earlier revision and its data assumptions are valid. Code rollback does not undo broker side effects or reverse a destructive migration.

### HPA as a separate stateless exercise

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: stateless-dashboard
  namespace: paper-school
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: stateless-dashboard
  minReplicas: 2
  maxReplicas: 4
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 60
```

This refers to a future `stateless-dashboard` deployment you must implement with external state and CPU requests. It is not applicable to the supplied SQLite lab. HPA needs a metrics source; CPU utilization targets depend on requests. Autoscaling cannot fix a provider's fixed account rate limit. [HPA documentation](https://kubernetes.io/docs/concepts/workloads/autoscaling/horizontal-pod-autoscale/)

### Jobs and schedules

A Job runs finite work; a CronJob creates scheduled Jobs. Use a migration Job with explicit approval, a bounded retry policy, and idempotent logic. For a morning task, specify `timeZone: Asia/Kolkata` on a supported cluster and define missed-run behavior. `concurrencyPolicy: Forbid` reduces overlap for that CronJob but does not make the task globally exactly once. [CronJob documentation](https://kubernetes.io/docs/concepts/workloads/controllers/cron-jobs/)

**Exercise:** Run a synthetic cache-refresh Job twice and prove its idempotent result. Then schedule it, suspend the CronJob, and inspect its state. Explain why a cluster CronJob cannot start a completely stopped cluster or VM on which it depends.

## Chapter 57 Deploy the paper image to GKE

### Cost and prerequisites

This is an optional billable exercise. Complete the local cluster first. Use a disposable project with billing, budget alerts, quota, and administrator-approved IAM. GKE can exceed a small single-VM monthly budget; the book does not promise a rupee ceiling. Read the current [Autopilot creation guide](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/creating-an-autopilot-cluster) and regional pricing before proceeding.

Run in Cloud Shell or a workstation with gcloud, Docker, kubectl, and the GKE authentication plugin available. Confirm versions and authorized project. The operator needs permissions for enabling APIs, creating the repository/cluster, building or pushing the image, and accessing the cluster. Do not solve missing permissions by granting Owner indiscriminately.

```bash
export PROJECT_ID="REPLACE_WITH_DISPOSABLE_PROJECT"
export REGION="asia-south1"
export CLUSTER="paper-school-gke"
test "$PROJECT_ID" != "REPLACE_WITH_DISPOSABLE_PROJECT" || exit 1
gcloud config set project "$PROJECT_ID"
gcloud services enable container.googleapis.com \
  artifactregistry.googleapis.com --project="$PROJECT_ID"

gcloud artifacts repositories create paper-school \
  --repository-format=docker --location="$REGION" \
  --project="$PROJECT_ID"

gcloud auth configure-docker "$REGION-docker.pkg.dev"
export IMAGE="$REGION-docker.pkg.dev/$PROJECT_ID/paper-school/paper-api:lesson1"
docker build -t "$IMAGE" .
docker push "$IMAGE"
```

The working directory must be the paper lab containing the Dockerfile. Authentication for pushing an image is not automatically authentication for nodes pulling it. Grant the cluster's actual image-pull identity repository reader access when required, and inspect ImagePullBackOff events instead of assuming the deployer's credentials are reused. [Artifact Registry image operations](https://docs.cloud.google.com/artifact-registry/docs/docker/pushing-and-pulling)

### Create and access the cluster

```bash
gcloud container clusters create-auto "$CLUSTER" \
  --region="$REGION" --project="$PROJECT_ID"
gcloud container clusters get-credentials "$CLUSTER" \
  --region="$REGION" --project="$PROJECT_ID"
kubectl config current-context
kubectl apply -f k8s/namespace.yaml
```

For this exercise, create `paper-gke.yaml` from `k8s/paper.yaml` and replace its `image: paper-school:lesson1` with the exact Artifact Registry image you just pushed. Keep the other safety properties. Inspect the resulting file before applying it:

```bash
kubectl apply -f paper-gke.yaml
kubectl -n paper-school rollout status deployment/paper-api --timeout=300s
kubectl -n paper-school get pods -o wide
kubectl -n paper-school port-forward service/paper-api 8015:80
```

Do not deploy the unresolved local-only image name to GKE. After deployment, inspect the actual Pod image reference and events. A registry image exists independently of your local kind image cache.

Keep access through the local port-forward or Cloud Shell's authorized preview. Do not create a public LoadBalancer for this lab. Test health, strategy save, one synthetic entry, and one synthetic target exit. Record admitted resource settings and workload events.

### Cleanup

After verifying the project/context and deciding the synthetic data is disposable:

```bash
gcloud container clusters delete "$CLUSTER" \
  --region="$REGION" --project="$PROJECT_ID"
gcloud artifacts repositories delete paper-school \
  --location="$REGION" --project="$PROJECT_ID"
```

These commands are destructive to the named learning resources and prompt for confirmation. Review any retained disks, addresses, log retention, and other resources separately. Deleting a cluster is not proof that every related charge has stopped.

## Chapter 58 Workload identity Cloud SQL and delivery pipelines

### Workload identity instead of key files

Workload Identity Federation for GKE lets workloads use Google Cloud authorization without baking service-account keys into images. Kubernetes service accounts and Google Cloud service accounts are distinct identities. Follow the current [GKE workload identity guide](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/workload-identity) for the chosen direct-principal or impersonation approach.

The following direct-principal example grants one service account access to one existing synthetic secret. It assumes an appropriately configured GKE workload identity pool and a Kubernetes service account named `paper-api` in `paper-school`. The base paper image does not read Secret Manager, so this is an identity exercise for an extended test workload, not a required permission for the original lab.

```bash
export PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" \
  --format='value(projectNumber)')"
export PRINCIPAL="principal://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/$PROJECT_ID.svc.id.goog/subject/ns/paper-school/sa/paper-api"

gcloud secrets add-iam-policy-binding paper-demo-secret \
  --project="$PROJECT_ID" \
  --member="$PRINCIPAL" \
  --role=roles/secretmanager.secretAccessor
```

Create only a harmless demonstration secret beforehand, enable Secret Manager, and use a reviewed Google client library in a separate test Pod. Validate allowed access and denial to a different secret. Do not print the retrieved value into shared logs; report only the outcome. Understand the cluster's metadata access and policy requirements before adding a deny-all egress policy.

### Cloud SQL connection reasoning

The Cloud SQL Auth Proxy or supported connectors can simplify authenticated encrypted connections. They still require instance connectivity, IAM permissions, and database authorization. A proxy process failing to connect does not mean a SQL password is wrong; distinguish infrastructure authentication, network reachability, and database login. [Cloud SQL Auth Proxy](https://docs.cloud.google.com/sql/docs/postgres/connect-auth-proxy)

Store a durable ledger in Cloud SQL only after implementing migrations and bounded pools. Estimate total possible connections as maximum Pods times pool size plus background and administrative clients. An autoscaling API can exhaust a database even when each Pod seems modest. Use graceful pool shutdown and retry only safe transactional operations.

### CI delivery without broker credentials

Separate build identity, deploy identity, and runtime identity. The build runs tests and creates an immutable image. The deployer updates a reviewed workload specification. The runtime reads only its scoped resources. Untrusted pull requests must not access client secrets or deploy to production. Use workload federation for CI where supported rather than long-lived downloaded keys.

**Exercise:** Write a pipeline diagram showing where source, dependencies, test artifacts, image digest, approvals, and deployment identity enter. Add a failure before rollout and another after partial rollout. Explain the rollback behavior without assuming a database migration can always be undone.

## Chapter 59 Infrastructure as code observability and cost control

### Terraform as a reviewed plan

This small example creates only a network and subnet in a disposable project. Install Terraform and a supported Google provider, authenticate through an approved method, then review the plan. It does not deploy the trading application.

```hcl
terraform {
  required_providers {
    google = {
      source = "hashicorp/google"
    }
  }
}

variable "project_id" { type = string }

provider "google" {
  project = var.project_id
  region  = "asia-south1"
}

resource "google_compute_network" "school" {
  name                    = "paper-iac-network"
  auto_create_subnetworks  = false
}

resource "google_compute_subnetwork" "school" {
  name          = "paper-iac-subnet"
  ip_cidr_range = "10.77.0.0/24"
  region        = "asia-south1"
  network       = google_compute_network.school.id
}
```

After your first reviewed initialization, pin a compatible provider constraint and commit the dependency lock file. Protect state; do not commit local state files containing sensitive resource information. Configure an appropriate protected remote backend and locking before team use. Read the plan for replacements, not merely the number of changed resources.

```bash
terraform init
terraform fmt -check
terraform validate
terraform plan -var="project_id=$PROJECT_ID" -out=paper.plan
terraform show paper.plan
terraform apply paper.plan
```

The apply command changes cloud resources. Run it only after confirming the learning project and reviewing the plan. At the end, review `terraform plan -destroy` before approving destruction of these named learning resources. Do not run a destroy from a production state directory.

### Operations evidence

Capture deployment version, Pod restarts, unknown orders, reconciliation lag, queue oldest age, active-position freshness, and user-visible request errors. Cluster CPU is not a trading correctness metric. Logs need account-safe correlation but should avoid raw secrets and uncontrolled high-cardinality labels.

```text
resource.type="k8s_container"
resource.labels.namespace_name="paper-school"
severity>=ERROR
```

This Cloud Logging filter is an exercise starting point. Plain stdout may not map to the severity you expect unless logging is structured appropriately. Search raw message text and inspect sample entries when a severity filter returns nothing.

### Cost and cleanup checklist

Inventory clusters, node/Pod resource billing, Cloud SQL instances, disks/PVC backing volumes, snapshots, external load balancers, NAT, public addresses, Artifact Registry storage, and logs. Set budget alerts, but remember they are not automatic hard caps. A PVC's reclaim policy affects whether backing storage remains. Verify deletion rather than assuming a removed namespace removed every cloud resource.

**Exercise:** Produce a before/after resource inventory and a cost estimate with the date and regional assumptions. Explain why a small VM may remain the better production choice for one client even after you learn Kubernetes thoroughly.

## Chapter 60 A full paper system capstone

### Required architecture

Build an authenticated API with external PostgreSQL, Redis queues, one account-scoped execution owner, a simulated market stream, reconciliation, and a browser dashboard. Package separate processes in containers. Deploy locally first and to a disposable cloud environment only after tests pass. No real broker token is needed.

The deployment should make ownership explicit: API replicas may scale after session and state externalization; strategy evaluation may scale within provider-style quotas; market feeds are partitioned by account; execution is serialized or fenced per account under a documented protocol; reconciliation deduplicates evidence; notifications are independent.

### Acceptance scenario

1. Provision infrastructure and schema from reviewed definitions.
2. Bootstrap one admin, verify email through a fake inbox, enroll TOTP, and sign in.
3. Save two strategies with different exit alerts and zero retries on one.
4. Deliver a duplicate signal and prove one intent is created.
5. Simulate a partial fill and a lost update, then reconcile the missing evidence.
6. Disconnect the dashboard while ticks and risk checks continue.
7. Replace the feed owner and restore subscriptions without forgetting positions.
8. Trigger one strategy's exit alert and preserve the other strategy's allocation.
9. Kill a worker after uncertain submission and recover without duplicate quantity.
10. Restore the ledger into an isolated environment and compare all accounting totals.

### Evidence to deliver

Include a context diagram, a deployment diagram, one transaction sequence diagram, an order state machine, a threat model, a test report, an incident report, a restore report, and a dated cost model. Show the exact software versions and which cloud steps you executed. Label unsupported assumptions instead of hiding them.

### Original Kubernetes review questions

Why can a Service exist without ready endpoints? Because its selectors may match nothing or its Pods may be unready. Why can a Pod be Pending? Scheduling, quota, resources, image/storage preparation, and policy can be involved; events narrow the cause. Why does a failed liveness probe differ from a failed readiness probe? One can cause restart; the other controls traffic eligibility. Why is a Secret not safe just because it is base64? Encoding is reversible and access policy still matters.

Why does HPA not solve a single broker's fixed quota? More workers can exceed the same external budget. Why is a StatefulSet not a database backup? Stable identity and volumes do not preserve an independent recoverable history. Why must an execution worker handle SIGTERM carefully? Stopping the local process does not cancel an in-flight external side effect. Why is `kubectl apply` not an acceptance test? It confirms object submission, not end-to-end business correctness.

### Certification boundary

These labs build practical evidence relevant to Google Cloud engineering, architecture, operations, and security. They do not replace every objective in the current exam guide. Kubernetes-specific certifications have their own current task scope and exam environment; verify those official guides separately if you choose that path. Keep the goal concrete: explain, implement, test, deploy, break, recover, and defend your design.

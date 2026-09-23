## Expansion 29

### Worked lesson inspect scope before creating resources

```bash
gcloud auth list
gcloud config list
gcloud projects describe "$PROJECT_ID"
gcloud services list --enabled --project="$PROJECT_ID"
gcloud compute instances list --project="$PROJECT_ID"
```

Run these read-only commands before a lab. Confirm the account, project ID, and region variables. A project display name is not necessarily its immutable ID. Avoid assuming that the Cloud Shell prompt and every command's explicit `--project` refer to the same project.

**Exercise:** Draw two client projects with separate billing linkage, secrets, VM identities, and data stores. Mark the developer's access explicitly. Then remove developer access in the diagram and explain how client operations and billing continue. Discuss source-code visibility honestly: a client with VM administrative access can inspect deployed source.

## Expansion 30

### Worked lesson separate deployer and runtime permissions

The deployer may need to create a VM and attach a service account. The runtime needs only its application resources. The ability to impersonate a powerful service account can become privilege escalation even when the human's direct role looks narrow.

```bash
gcloud iam service-accounts create paper-runtime \
  --display-name='Paper application runtime' \
  --project="$PROJECT_ID"

gcloud iam service-accounts describe \
  "paper-runtime@$PROJECT_ID.iam.gserviceaccount.com" \
  --project="$PROJECT_ID"
```

This creates an identity but grants it no application permissions. Add only the required resource-scoped bindings in the relevant lab. Do not download a JSON key simply because it is easy. Prefer workload identity on managed infrastructure and controlled impersonation for humans.

**Exercise:** Produce a permission matrix for deployer, API, execution, analytics, and support reader. Explain why support staff can inspect sanitized logs without decrypting broker credentials. Test a denied permission and record its expected error.

## Expansion 31

### Worked lesson follow a packet

For a browser request to your public VM: DNS resolves the name, routing reaches the external address, firewall policy permits 443, Nginx terminates TLS, the proxy connects to loopback Uvicorn, and the application checks the session. A failure at any hop can resemble "the app is down" but requires different evidence.

```bash
gcloud compute addresses describe "$IP_NAME" \
  --region="$REGION" --project="$PROJECT_ID"
gcloud compute instances describe "$VM_NAME" \
  --zone="$ZONE" --project="$PROJECT_ID" \
  --format='get(networkInterfaces[0].accessConfigs[0].natIP)'
```

Compare reservation and attachment. On the VM, `ss -ltnp` distinguishes a listening process from a firewall problem. A route for outbound traffic does not automatically allow inbound requests. Cloud NAT supports outbound connections for appropriate private workloads; it is not an inbound public reverse proxy.

**Exercise:** Deliberately use the wrong port in a local proxy configuration, then distinguish connection refused from authentication denied. Do not edit a production firewall merely to reproduce the lab.

## Expansion 32

### Worked lesson restart evidence

```bash
sudo systemctl show ashuchart-api \
  -p MainPID -p ExecStart -p EnvironmentFiles --no-pager
sudo systemctl status ashuchart-api --no-pager -l
sudo journalctl -u ashuchart-api -b -n 80 --no-pager
```

`MainPID` identifies the running process. `ExecStart` identifies the interpreter and command. `EnvironmentFiles` identifies configuration sources. None of these alone proves the application's dependencies are healthy. Inspect a sanitized readiness response and broker state separately.

A unit edit requires daemon-reload; an environment-value edit requires process replacement to load the value. Repeated restarts without understanding the failure can conceal the original error and create execution ownership contention.

**Exercise:** In a disposable service, log only a nonsecret configuration version at startup. Change the environment file without restart and prove the old process retains its old configuration. Restart deliberately, then verify the new version and PID.

## Expansion 33

### Worked lesson distinguish three connection failures

```text
HTTP 401/403: application identity or permission was rejected
HTTP 426: ordinary HTTP reached a WebSocket-only path
HTTP 502: proxy could not obtain a valid upstream response
```

These are diagnostic categories, not an exhaustive mapping of every proxy implementation. Inspect browser network details, API logs, and Nginx error logs together. A normal `curl` request to a WebSocket endpoint does not perform the browser's authenticated upgrade handshake.

```bash
sudo nginx -t
sudo journalctl -u nginx --since '10 minutes ago' --no-pager
sudo tail -n 60 /var/log/nginx/error.log
```

Redact request URLs if they contain secrets before sharing output. Prefer webhook credentials in supported headers; if an external sender requires a query credential, minimize its exposure in access logs and rotate it under a controlled process.

**Exercise:** Make a browser test verify that an unauthorized WebSocket connection is rejected while an authorized one opens. Then stop the broker simulator and prove the dashboard remains available to report degraded broker status.

## Expansion 34

### Worked lesson write a restore acceptance test

A backup drill must specify what success means. For a trading ledger: all strategy versions referenced by open positions exist; execution IDs remain unique; total owned quantity matches the restored fill ledger; credentials can be recovered through the approved key process; and the restarted worker does not replay already-submitted orders blindly.

```text
Restore checkpoint
  record backup timestamp and schema version
  restore into an isolated database
  run integrity and quantity queries
  start paper workers with external network orders disabled
  replay duplicate broker evidence
  compare before and after totals
  record elapsed recovery time
```

Choose RPO and RTO from business requirements, then measure whether the backup and deployment process can meet them. A claimed five-minute RTO is not credible if restoring the database takes forty minutes.

**Exercise:** Produce a monthly cost worksheet with compute, persistent disk, snapshots, public IP, logging, database, and network entries. Set one workload to eight hours per weekday but keep persistent resources billed for their actual retention. Explain why scheduling is not a universal budget cap.

## Expansion 35

### Worked lesson image versus container versus Pod

An image is a versioned filesystem and startup description. A container is a process instance created from it. A Pod is Kubernetes' scheduling unit that can contain one or more containers sharing networking and selected volumes. A Deployment describes how controllers maintain and replace Pods.

```text
Python source -> container image -> registry digest
                                   |
                            Deployment specification
                                   |
                           ReplicaSet -> Pod -> process
```

Use an image digest or a controlled immutable tag for releases. A mutable `latest` tag makes it harder to know what code is running. A restart is not a database migration, and scaling a Deployment does not make an embedded SQLite database shared.

**Exercise:** Follow the extended Kubernetes labs later in this edition. Explain which examples are intentionally single-replica and why. Do not enable an HPA for the SQLite lab simply because autoscaling appears in an exam objective.

## Expansion 36

### Worked lesson event schema evolution

```json
{
  "event_id": "paper-fill-17",
  "schema_version": 2,
  "type": "fill_confirmed",
  "account_id": "synthetic-account",
  "event_time": "2026-09-18T04:00:00Z",
  "ingested_at": "2026-09-18T04:00:02Z",
  "quantity": 2,
  "price": "100.25"
}
```

Event time describes the business occurrence; ingestion time describes your observation pipeline. Late arrivals can change historical aggregates. Additive schema changes are often easier than changing the meaning of an existing field, but consumers still need validation and version policy.

**Exercise:** Add a fee field without breaking a version-one consumer. Then change price from a decimal string to a nested object and demonstrate why it is a breaking change. Quarantine invalid records with sanitized reasons and replay them after repair without duplicating accepted events.

## Expansion 37

### Worked lesson compute drawdown

```python
equity = [100, 110, 105, 90, 108, 120]
peak = equity[0]
worst = 0.0
for value in equity:
    peak = max(peak, value)
    drawdown = (peak - value) / peak
    worst = max(worst, drawdown)
assert round(worst * 100, 2) == 18.18
```

This curve has a decline from 110 to 90 before reaching a new high. A positive final return does not reveal the depth of interim loss. Track both returns and risk measures, and state whether the curve includes fees and unrealized marks.

**Exercise:** Change execution from same-bar close to next-bar open and observe the difference. Include a missing candle and a gap. If target and stop both lie within one bar, implement a conservative ambiguity policy and compare results with finer-grained data where available.

## Expansion 38

### Worked lesson a leakage resistant baseline pipeline

```python
# Requires scikit-learn in a separate ML learning environment.
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

def fit_baseline(features, labels, split_index):
    model = make_pipeline(StandardScaler(), LogisticRegression(max_iter=500))
    model.fit(features[:split_index], labels[:split_index])
    probabilities = model.predict_proba(features[split_index:])
    return model, probabilities
```

The scaler is fit only on the training slice because it is inside the fitted pipeline. This is only a basic chronological split; overlapping target windows and delayed feature availability can still leak information. Require both classes in training and validate the feature schema.

**Exercise:** Use synthetic operational metrics, not real client data, and compare the model with a simple threshold rule. Report precision and recall for the rare failure class. Add an abstain policy when required features are missing rather than filling them with invented zeros.

## Expansion 39

### Worked lesson evaluate retrieval separately from generation

```python
expected = {"q1": {"runbook-redis"}, "q2": {"runbook-ws", "runbook-nginx"}}
retrieved = {"q1": ["runbook-auth", "runbook-redis"], "q2": ["runbook-ws"]}
recalls = []
for question, relevant in expected.items():
    found = set(retrieved[question])
    recalls.append(len(found & relevant) / len(relevant))
assert sum(recalls) / len(recalls) == 0.75
```

This measures retrieval coverage for a tiny labeled example, not answer correctness. Evaluate whether a generated answer cites the retrieved evidence faithfully and distinguishes current from historical instructions. A correct-sounding answer with the wrong client account context is still a security failure.

**Exercise:** Add tenant metadata and a query from a different account. Verify filtering occurs before results reach the model. Test document deletion and re-indexing so removed sensitive text does not remain retrievable indefinitely.

## Expansion 40

### Worked lesson build an exam gap tracker

```csv
objective,explain_without_notes,lab_evidence,review_date,next_action
IAM least privilege,yes,iam-denied-read.md,2026-09-23,repeat with workload identity
GKE probes,no,,2026-09-23,complete probe failure lab
Database restore,yes,restore-report.md,2026-09-23,repeat timed restore
```

The tracker records evidence rather than a vague feeling of readiness. Add every objective from the current guide for the exam you actually intend to take. Recheck the standard guide versus renewal guide; they can have different scope and format.

**Exercise:** Pick one incorrect practice answer and trace it to an objective, a misunderstood rule, and a small lab. Explain why the wrong choices fail the scenario constraints. Memorizing a product name without its constraints is weak preparation.

## Expansion 41

### Worked lesson one problem three professional perspectives

Scenario: a client requires recovery after a zonal outage, restricted access to broker secrets, and audit history for all executions. The architect defines recovery goals, ownership, and a costed design. The DevOps engineer implements deployment, monitoring, failover drills, and safe handover. The security engineer reviews identities, secret access, evidence retention, and recovery abuse.

The database engineer tests backup consistency and ledger integrity. The network engineer investigates private connectivity and egress identity. The data engineer governs downstream analytics. The ML engineer ensures advisory models do not learn from leaked or unavailable-at-decision data. These roles overlap, but they are not identical exams or interchangeable job titles.

**Exercise:** Write one page from each of three perspectives, then identify a conflict. For example, broader log retention can help incident investigation but increase sensitive-data exposure and cost. Resolve it with scoped retention and redaction rather than pretending one requirement cancels the other.

## Expansion 42

### Additional scenario questions

**Question:** A Pod is Running but not Ready. Should you immediately increase replicas? **Answer:** Inspect readiness and dependency evidence first. More copies of the same misconfiguration will not repair it.

**Question:** An HPA has no CPU utilization value. What do you inspect? **Answer:** Metrics availability and resource requests, then HPA events. The target is calculated relative to requests for the relevant configuration, not an arbitrary machine percentage.

**Question:** A PVC survived Pod replacement. Is it a backup? **Answer:** No. It is persistent storage; deletion, corruption, and regional failure still need a backup and restore plan.

**Question:** An image push succeeded but a Pod reports ImagePullBackOff. What differs? **Answer:** The identity and network path pulling the image may differ from the deployer's push identity. Check reference, permissions, registry, and events.

**Question:** You enabled two replicas of a websocket owner. Is twice the feed availability guaranteed? **Answer:** No. You may exceed broker connection limits or create conflicting subscriptions. Partition ownership deliberately.

**Exercise:** Turn each answer into a reproducible paper-only failure drill and write the evidence that distinguishes it from a similar symptom.

## Expansion 43

### Worked lesson definition of done for one milestone

For breakout monitoring, done means the saved configuration roundtrips; a closed candle is selected correctly; its threshold is persisted; TTL and entry-end boundaries are honored; duplicate signals do not create duplicate watches; a restart preserves the remaining deadline; one threshold crossing creates at most one intent; a kill switch blocks the entry; and the browser displays waiting, expired, and triggered states distinctly.

```text
Milestone evidence
  specification.md
  decision-record.md
  tests for normal and boundary cases
  one failure injection report
  browser screenshot or trace where applicable
  limitations.md
```

**Exercise:** Apply the same definition-of-done method to email verification, CNC restoration, and a cloud backup job. A feature's existence in a menu is not its acceptance criterion.

## Expansion 44

### Worked lesson evidence driven incident notes

```text
Observed: dashboard /ws/feed returned HTTP 426
Impact: browser did not receive live dashboard messages
Known: HTTP API routes returned responses
Unknown: whether Dhan ticks reached the market-feed process
Hypothesis: proxy did not forward upgrade headers
Next check: inspect active Nginx configuration and authenticated handshake
Containment: do not assume dashboard prices are fresh
```

Separate facts from inference. Later append the confirmed cause, fix, validation, and prevention. Avoid rewriting the original uncertainty as though you knew the cause at the beginning. This habit makes incident reports credible and prevents premature fixes to unrelated components.

**Exercise:** Write the same structure for `AuthenticationError`, `CFG_MISSING`, and `UNKNOWN_ORDER_OUTCOME`. Include a safe diagnostic command and a dangerous action you explicitly avoid, such as deleting Redis state or resubmitting blindly.

## Expansion 45

### Worked lesson an architecture decision record

```text
Decision: keep the broker feed owner on a supervised VM initially
Context: one client, modest subscription set, continuous outbound socket
Alternatives: VM worker, GKE worker, request-driven managed service
Chosen because: straightforward ownership and predictable process lifecycle
Costs: VM patching, single-instance limitation, explicit recovery work
Revisit when: measured scale or availability goals exceed this design
Evidence: reconnect soak test and operational runbook
```

This is stronger than saying a VM is always better than Kubernetes. An interview answer should be conditional on requirements and measured constraints. Explain which failure you accept, which you mitigate, and how you know the mitigation works.

**Exercise:** Present your design in ten minutes. Ask a peer to introduce one new constraint, such as 100 clients or regional outage recovery. Revise the design without abandoning established quantity and ownership invariants.

## Expansion 46

### Worked lesson self assessment through independent implementation

Choose a small feature, close the book, and implement it from a written specification. Run tests you prepared before coding. Explain the design to another person, then ask them to change a requirement. Being able to adapt is stronger evidence than reproducing memorized source.

Use this sequence for a monthly review: implement a pure rule; add a validated route; persist it; test concurrent access; demonstrate it in a browser; deploy it to a disposable environment; inject failure; recover; and write a short explanation. Repeat with a different feature so the exercise tests transferable skill.

**Exercise:** Maintain a learning journal with one incorrect assumption per week and the evidence that changed it. Examples include assuming every green status badge proves live data, assuming accepted equals filled, or assuming a budget alert stops billing. The goal is better judgment, not a claim that you never make mistakes.

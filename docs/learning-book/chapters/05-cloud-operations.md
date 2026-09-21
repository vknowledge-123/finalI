# Part Five Google Cloud and Production Operations

## Chapter 29 Cloud fundamentals and resource hierarchy

Cloud computing gives you programmable access to compute, storage, networking, identity, and managed services. It does not remove architecture decisions. A VM is still a running operating system with patching, filesystem capacity, network access, and process supervision to manage.

An organization can contain folders and projects. A project groups resources, API enablement, quota, and IAM policies. A billing account pays for linked projects. IAM answers who can do which operation on which resource; billing linkage is not itself permission to administer every workload.

For client isolation, use a dedicated project and separate identities, secrets, databases, and deployment state. Identical local usernames on separate VMs do not inherently mix clients. Copying a populated Redis database, shared broker token, production `.env`, or globally configured webhook URL can. A VM administrator can normally read code and secrets available to that VM; obfuscation is not a strong boundary against the owner of the machine.

A region is a geographical area; a zone is a deployment location within it. Mumbai is `asia-south1`, with zones such as `asia-south1-b`. A zonal outage can stop a single VM. Choosing another zone may solve a capacity error but does not create high availability for an existing one-VM deployment.

Learn the difference between quota and capacity. Quota is a project/account limit; capacity is currently available provider infrastructure. `ZONE_RESOURCE_POOL_EXHAUSTED` does not imply your Python code is broken. An under-200-GB disk performance warning does not mean Google created a 200-GB disk; inspect the actual disk resource.

**Lab:** In a disposable project, list enabled APIs, assigned IAM roles, regions, zones, and billing linkage. Draw which resources are global, regional, and zonal. Explain what would survive deleting a VM versus deleting its boot disk versus deleting the project.

## Chapter 30 IAM service accounts and secrets

A service account represents a workload, not a person. Attach a minimally privileged service account to the VM instead of distributing long-lived JSON keys. Local development can use Application Default Credentials or controlled impersonation. Human administrator privileges and application runtime privileges should be separate. [Google Cloud service accounts](https://docs.cloud.google.com/iam/docs/service-account-overview)

Use predefined roles where they fit. A custom role is useful when a narrow operation, such as starting and stopping a particular VM, needs fewer permissions than a broad administrator role. Understand both the permission to act on a resource and the permission to attach or impersonate a service account.

Prefer OS Login and IAP for controlled SSH rather than exposing port 22 to the whole internet. IAP access requires both appropriate IAM and a firewall path for its documented source range. A firewall rule without IAM or IAM without a network path is insufficient.

Secret Manager stores versioned secrets and provides access auditing. Grant the runtime access only to the secrets it needs. Do not put secret values into Terraform variables that will be retained in state without a deliberate protection plan. Environment variables are a delivery mechanism, not encryption. Root and sufficiently privileged process inspectors can still read them. [Secret Manager practices](https://docs.cloud.google.com/secret-manager/docs/best-practices)

Encryption at rest for disks does not replace application protection of broker tokens or transport encryption. A Fernet key stored beside ciphertext protects against some accidental exposures but not a full compromise that reads both. Plan rotation and backup access together: losing the only decrypting key can make credentials unrecoverable.

**Lab:** Create two service accounts, one for the API and one for an analytics export. Grant the exporter access to a test bucket but not broker secrets. Prove a forbidden read fails. Record the denied audit event. Revoke permission and verify what happens to already-issued credentials and cached secrets.

## Chapter 31 Network design and a safe VM exercise

A VPC is a software-defined network. Subnets are regional. Firewall rules control permitted traffic; routes control where packets go. A public static address gives a stable endpoint, but does not make an application secure. DNS maps a domain to addresses. TLS authenticates the endpoint and encrypts transport when validated correctly.

The following Cloud Shell commands create billable learning resources. Use a new project you own, confirm its billing and budget, and do not substitute a production client project. Cloud Shell is already authenticated; a local Git Bash installation needs Google Cloud CLI authentication first. Replace the placeholder deliberately.

```bash
export PROJECT_ID="REPLACE_WITH_LEARNING_PROJECT_ID"
export REGION="asia-south1"
export ZONE="asia-south1-b"
export VM_NAME="trading-school-vm"
export IP_NAME="trading-school-ip"
test "$PROJECT_ID" != "REPLACE_WITH_LEARNING_PROJECT_ID" || exit 1
gcloud config set project "$PROJECT_ID"
gcloud services enable compute.googleapis.com
gcloud compute networks create trading-school --subnet-mode=custom
gcloud compute networks subnets create trading-school-subnet --network=trading-school --range=10.42.0.0/24 --region="$REGION"
gcloud compute addresses create "$IP_NAME" --region="$REGION"
export STATIC_IP="$(gcloud compute addresses describe "$IP_NAME" --region="$REGION" --format='value(address)')"
test -n "$STATIC_IP" || exit 1
gcloud compute firewall-rules create trading-school-web --network=trading-school --allow=tcp:80,tcp:443 --source-ranges=0.0.0.0/0 --target-tags=trading-school-web
gcloud compute firewall-rules create trading-school-iap-ssh --network=trading-school --allow=tcp:22 --source-ranges=35.235.240.0/20 --target-tags=trading-school-web
gcloud compute instances create "$VM_NAME" --zone="$ZONE" --machine-type=e2-medium --boot-disk-size=20GB --image-family=ubuntu-2404-lts-amd64 --image-project=ubuntu-os-cloud --subnet=trading-school-subnet --tags=trading-school-web --address="$STATIC_IP" --no-service-account --no-scopes --metadata=enable-oslogin=TRUE
gcloud compute ssh "$VM_NAME" --zone="$ZONE" --tunnel-through-iap
```

The no-service-account choice is intentional for a first OS/network exercise. Grant your human identity the required IAP tunnel and OS Login permissions through an authorized project administrator before SSH. Later attach a dedicated runtime account when the app needs Secret Manager or Cloud Storage. Do not grant Owner to solve every permission error.

The one-line commands avoid the trailing-backslash whitespace problem you encountered. After every resource creation, inspect the result and stop if it failed. Shell variables disappear when a session is replaced; an empty `$VM_NAME` explains many resource parsing errors.

Do not run the unauthenticated reference lab on public port 80. Initially access it using an SSH tunnel to its loopback port. Public HTTPS deployment is a later exercise after your authentication chapters are implemented and tested.

**Lab:** Run `df -h /`, `free -h`, `lsblk`, and `ss -ltnp` on the VM. Explain disk space versus RAM versus swap. Swap can reduce an abrupt out-of-memory failure, but disk-backed paging can severely delay a real-time application. It is not extra fast RAM.

## Chapter 32 Linux process supervision and deployment

systemd supervises long-running processes. `active` means the service process is running, not that broker authentication, subscriptions, data freshness, and exits work. Use application readiness in addition to OS process status.

Run under a dedicated non-root account. Keep code, writable data, and secrets in separate locations. Use `/opt/trading-school` for code, `/var/lib/trading-school` for application data, and a root-owned environment file for secrets. The reference lab remains loopback-only and paper-only.

This unit is a teaching template for an authenticated rebuild, not a command to replace your deployed client service:

```ini
[Unit]
Description=Trading School API
After=network-online.target redis-server.service
Wants=network-online.target

[Service]
Type=simple
User=trading-school
Group=trading-school
WorkingDirectory=/opt/trading-school
EnvironmentFile=/etc/trading-school.env
ExecStart=/opt/trading-school/.venv/bin/python -m uvicorn school.main:app --host 127.0.0.1 --port 8015
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
NoNewPrivileges=true
PrivateTmp=true
UMask=0077

[Install]
WantedBy=multi-user.target
```

`school.main` is the module you will create in your rebuild, not a module supplied in the small lab. Repeat the supervision pattern for alert, market-feed, execution, and reconciliation entry points only after those modules exist. Dependencies such as `After=` order startup; they do not prove Redis authentication succeeded.

Changes to a unit require `sudo systemctl daemon-reload`. Changes to an environment file require restarting the affected process to load new values; daemon-reload alone does not inject variables into it. Verify which EnvironmentFile each unit actually reads. Avoid dumping all environment values into a support chat.

Deploy an immutable release or a reviewed commit, install dependencies in the intended virtual environment, run migrations, run offline checks, then hand over workers carefully. Preserve local changes; do not use a force reset on a client's machine as a routine update command. Record the commit and deployment time.

**Lab:** Deliberately point a test unit at a missing module and inspect `journalctl`. Then use a wrong Redis password and distinguish import failure from authentication failure. Repair the source configuration, restart only the needed service, and confirm readiness rather than repeatedly restarting everything.

## Chapter 33 Nginx HTTPS and browser WebSockets

Nginx can terminate TLS and proxy requests to loopback Uvicorn. The dashboard WebSocket traverses this proxy; the outbound broker WebSocket does not. This explains why fixing browser `426 Upgrade Required` does not by itself repair a Dhan connection.

For an authenticated rebuild, a representative proxy configuration is:

```nginx
# Place map in the http context, not inside server or location.
map $http_upgrade $connection_upgrade {
    default upgrade;
    '' close;
}

server {
    listen 80;
    server_name trading.example.com;
    location / {
        proxy_pass http://127.0.0.1:8015;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_read_timeout 120s;
    }
}
```

For Nginx versions like the 1.24 installation in your logs, explicit HTTP/1.1 and upgrade forwarding are important. A successful WebSocket handshake uses HTTP 101. The application can still deny an unauthenticated handshake. `proxy_read_timeout` does not replace heartbeat and reconnect handling. [Nginx WebSocket documentation](https://nginx.org/en/docs/http/websocket.html)

Before using a certificate tool, replace the example domain with a domain you control and point its DNS to the reserved IP. Confirm the active server block, not merely a file that was never enabled. Follow the current [Certbot Nginx instructions](https://certbot.eff.org/instructions) for your OS and installation method. A typical invocation after installation is `sudo certbot --nginx -d your-real-domain.example`; use your real registered domain, not the literal example. Check `sudo certbot renew --dry-run` and the installed renewal timer.

Enable secure cookies when HTTPS is active. Trust forwarded headers only from your actual proxy. Do not allow arbitrary internet clients to spoof trusted client-IP or scheme headers by exposing the application port publicly.

**Lab:** Remove the upgrade header in a disposable deployment, observe the browser failure, restore it, and verify HTTP 101 in browser developer tools. Stop Uvicorn and observe the difference: Nginx may return 502, which is an upstream failure rather than a WebSocket-only failure.

## Chapter 34 Observability scheduling backups and cost

Build a dashboard of queue age, unknown orders, reconciliation lag, active positions lacking fresh prices, event-loop lag, broker error classes, Redis availability, disk usage, and descriptor count. Avoid labels containing arbitrary request IDs or every raw symbol on every metric; uncontrolled cardinality makes monitoring expensive. Use logs or traces for high-cardinality detail.

An SLI measures behavior, an SLO states a target, and an error budget describes allowed unreliability against that target. Separate market-session objectives from overnight periods. A feed-freshness objective should not declare an idle illiquid stock failed solely because it did not trade. Tie monitoring to provider heartbeat and subscription evidence too.

Useful read-only operations on a deployed instance include:

```bash
systemctl is-active ashuchart-api ashuchart-alert ashuchart-market-feed ashuchart-execution ashuchart-reconciliation
sudo journalctl -u ashuchart-market-feed --since '10 minutes ago' --no-pager
sudo journalctl -u ashuchart-api --since '10 minutes ago' --no-pager
sudo journalctl --disk-usage
df -h /
free -h
sudo ss -ltnp
```

Collect logs in UTC when correlating services across machines; display IST separately. `journalctl -f` follows new lines and exits with Ctrl+C. The pager exits with `q`. Redirecting output with `>` writes to a file, so no terminal output is expected.

Set journal retention and application log rotation according to your incident and audit needs. Do not delete critical trading records merely to free space. Back up the ledger, configuration, and encrypted recovery material; verify restoration into a separate environment. A snapshot is not automatically application-consistent, and a backup is not useful until its restore procedure has been exercised.

### Scheduling

An in-VM timer cannot start a stopped VM. Use Compute Engine instance schedules or an external scheduler for power operations. Google documents instance start/stop schedules and associated permissions. Scheduled times are not a real-time guarantee, so allow startup and reconciliation margin before accepting signals. [VM schedules](https://docs.cloud.google.com/compute/docs/instances/schedule-instance-start-stop)

A service-restart timer can use an explicit calendar timezone:

```ini
[Timer]
OnCalendar=Mon..Fri *-*-* 09:00:00 Asia/Kolkata
Persistent=false
Unit=trading-school-restart.service
```

Validate with `systemd-analyze calendar 'Mon..Fri *-*-* 09:00:00 Asia/Kolkata'`. The referenced service must exist and implement a safe handover. `Persistent=true` can trigger a missed calendar event after boot, which may be undesirable for a trading restart. A weekday schedule does not encode exchange holidays. Do not stop a VM while it is responsible for unresolved orders or needed exit monitoring.

### Cost model

Compute hours are only one line item. Add disks, snapshots, reserved/public IPv4 charges as applicable, network egress, logging, load balancers, managed databases, and taxes. A stopped VM can still incur charges for retained resources. Budget alerts notify; they are not an automatic spending cap. [Cloud Billing budgets](https://docs.cloud.google.com/billing/docs/how-to/budgets)

Estimate monthly cost as `running_hours * compute_rate + provisioned_storage + IP/network + managed_services + observability`. Use the current regional pricing calculator, not a fixed rupee promise. An 08:00-16:00 weekday schedule is approximately eight hours times the actual weekdays, but broker authentication and preparation time still need planning. Measure the application before downsizing RAM.

**Lab:** Restore a database backup, compare row counts and fill totals, and document recovery time and data-loss window. Then inventory and remove your disposable resources explicitly. Stopping a VM is not cleanup of disks, reserved addresses, buckets, or endpoints.

## Chapter 35 Containers Terraform and managed runtime choices

A container packages a process and its filesystem dependencies, not a whole independent kernel. Build a non-root image, avoid secrets in layers, keep dependencies pinned, and scan the resulting artifact. Docker Compose is useful for a local API, Redis, PostgreSQL, and fake broker stack. It is not automatically high availability.

Terraform represents desired infrastructure state. Learn provider configuration, variables, resources, outputs, plans, state, and locking. Review a plan before apply. State can contain sensitive information; secure the backend and access controls. Import existing resources rather than creating duplicate production infrastructure accidentally. Prefer a disposable project to learn destroy operations.

Use GKE to learn Pods, Deployments, Services, ConfigMaps, Secrets, probes, resource requests/limits, rollout, and workload identity. A Kubernetes Secret is not automatically safe because it is base64-encoded. A Deployment with two replicas of an execution worker can double-order unless ownership is designed for it. Readiness probes should not cause a storm of broker calls.

Cloud Run can host appropriate stateless HTTP services and supports WebSockets, but connection timeout and multi-instance state synchronization still matter. Long-lived feed ownership and account-scoped order execution need an explicit design; do not assume a VM worker can be moved unchanged into a request-driven service. [Cloud Run WebSockets](https://docs.cloud.google.com/run/docs/triggering/websockets)

Learn service selection by workload:

| Need | Candidate | Trade-off to explain |
|---|---|---|
| Long-running broker owner | Compute Engine or designed container worker | You manage supervision and recovery |
| Stateless authenticated HTTP | Cloud Run | Runtime lifecycle and external state |
| Many orchestrated workloads | GKE | Powerful controls, more operational concepts |
| Relational ledger | Cloud SQL PostgreSQL | Managed operations and ongoing cost |
| Ephemeral shared cache | Memorystore | Network access and durability requirements |
| Object archives | Cloud Storage | Object semantics, not row transactions |
| Analytics across large history | BigQuery | Query economics, not order-path OLTP |

**Lab:** Containerize the paper API, then deploy only that API to a managed runtime. Keep its database external, document cold-start and connection-pool behavior, and test a rollout. Compare this with the VM deployment using measured complexity and cost, not fashion.


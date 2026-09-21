# Part Six Data Engineering Machine Learning and Certification

## Chapter 36 Build an analytics path outside execution

Operational databases answer questions such as "is this position already exiting?" Analytics systems answer questions such as "what was the distribution of entry latency over six months?" Different access patterns justify different storage. Never make a live exit wait for a warehouse query.

Export sanitized domain events from the outbox into an analytics path. An event should contain a stable ID, schema version, event time, ingestion time, account scope, event type, and payload. Deduplicate on event identity. Separate event time from processing time so delayed arrivals are not assigned to the wrong market interval.

Start with daily Parquet files in Cloud Storage and a batch warehouse load. Add streaming only when the freshness requirement justifies it. Pub/Sub can decouple producers and consumers, but redelivery and acknowledgment behavior still require idempotent consumers. Dataflow is useful for managed Apache Beam pipelines with event-time windows and late-data handling. Dataproc is relevant when existing Spark/Hadoop workloads justify that environment. These are alternative tools, not mandatory steps on every event.

Partition BigQuery tables by an appropriate date and cluster by fields frequently used for selective filtering. Require partition filters where suitable and inspect bytes processed before running expensive queries. Small operational updates belong in the ledger, not a stream of warehouse polling requests. [BigQuery partitioning](https://docs.cloud.google.com/bigquery/docs/partitioned-tables)

```sql
-- Illustrative BigQuery query after you create the analytics schema.
SELECT
  strategy_id,
  COUNT(*) AS submissions,
  APPROX_QUANTILES(submission_latency_ms, 100)[OFFSET(95)] AS p95_ms
FROM `learning_project.paper_events.submissions`
WHERE event_date BETWEEN DATE '2026-09-01' AND DATE '2026-09-07'
GROUP BY strategy_id;
```

Do not claim these dates contain real trading results. Generate synthetic data for the lab. Add schema checks, missing-value rules, expected row counts, lineage, access policy, and retention. If a source field changes its type, quarantine bad events rather than silently turning every price into zero.

**Lab:** Generate 10,000 paper fills and latency events, write a partitioned archive, load an analytics table, and verify duplicate imports do not double totals. Simulate one-hour-late data. Explain why an at-least-once pipeline needs business-key deduplication even if transport is reliable.

## Chapter 37 Statistics and honest backtesting

Before ML, learn mean, median, variance, standard deviation, quantiles, probability, conditional probability, correlation, sampling, and confidence intervals. Learn how a small number of outliers can make average latency or average trade return misleading. Correlation does not establish causation.

A backtest is a simulator using historical information. At decision time it must use only information that would actually have been available. A candle's final high is not known at its opening. If you choose symbols using today's surviving list, you can introduce survivorship bias. If you tune parameters repeatedly on the same test period, you have used that test period for training.

Use chronological train, validation, and test splits for time-dependent problems. Fit transformations only on training data. Where labels overlap across time, consider purging overlapping examples and an embargo around split boundaries. Compare with simple baselines before celebrating a complex model.

Include transaction costs, slippage assumptions, delayed entries, partial fills, circuit restrictions, and unavailable instruments in the simulator's limitations. Mark ambiguous intrabar outcomes. If both stop and target are inside the same OHLC candle, that candle alone does not reveal which happened first.

Track drawdown, turnover, exposure, distribution of returns, and sensitivity to costs rather than only win rate. A strategy can win often and lose money because occasional losses dominate. A promising backtest is not evidence that a system is safe to trade live or compliant with applicable rules.

**Lab:** Generate a synthetic trending and a synthetic random-walk price series. Implement a simple moving-average signal with next-bar execution. Then deliberately introduce look-ahead by using future data, observe the improvement, and explain why it is invalid. Repeat with higher costs and report the change honestly.

## Chapter 38 Machine learning and MLOps

ML estimates a mapping from features to outcomes. A training job minimizes a loss on examples; evaluation measures generalization on held-out data. Features must be available at prediction time. Labels must be defined before you choose a model. "Predict profitable trades" is not a complete label definition.

For a safer project extension, predict operational anomalies, such as unexpectedly delayed feed updates, using sanitized metrics. Keep predictions advisory. Another useful exercise is classifying sanitized error messages into categories for an operator, with confidence and an abstain option. Neither system needs broker trading permissions.

Learn regression versus classification, train/validation/test separation, overfitting, regularization, class imbalance, precision, recall, ROC/PR curves, and calibration. Accuracy is weak when almost every example is healthy. If missing a real outage is expensive, discuss false-negative cost explicitly rather than maximizing one generic score.

Start locally with a reproducible scikit-learn pipeline. Pin random seeds where applicable, record data versions and feature transformations, and compare against a constant or simple rule baseline. A seed does not guarantee bit-for-bit reproducibility across every platform and library version.

Map the workflow to Google's managed ML services: store data, run a training job, track an experiment, register an artifact, evaluate it, deploy batch or online inference, monitor, and retrain under controlled approval. Your attachment calls this Vertex AI; current documentation and exam guides may use updated Gemini Enterprise Agent Platform names for some capabilities. Follow the linked exam guide's terminology rather than assuming a service name remains unchanged.

MLOps applies software and data controls to models. Version code, data, features, parameters, and artifacts together. Training-serving skew occurs when the feature pipeline differs between training and inference. Data drift means the input distribution changed; concept drift means the relationship with the target changed. Neither automatically proves retraining will help.

**Lab:** Train an advisory latency-anomaly model, publish a model card with limitations, and compare batch predictions against a rule baseline. Deploy only in a disposable project, set resource limits, then remove the endpoint after the exercise. Real-time endpoints and accelerators can keep accruing cost even when you are not using the browser.

## Chapter 39 Embeddings vector databases and retrieval

An embedding maps content to a numeric vector. Similarity search retrieves nearby vectors according to a metric. It does not prove factual correctness. A vector database stores embeddings and metadata and may index them approximately to trade exactness for speed and memory.

Use this technology for a read-only operations assistant over sanitized runbooks, architecture decisions, and known error explanations. Do not embed broker secrets, TOTP seeds, full customer identities, or unrestricted production logs. A retrieval system needs access control before returning results, not only before asking the language model to write an answer.

```python
from math import sqrt

def cosine(a, b):
    if len(a) != len(b) or not a:
        raise ValueError("Vectors must have the same nonzero dimension")
    aa = sum(x * x for x in a)
    bb = sum(x * x for x in b)
    if aa == 0 or bb == 0:
        raise ValueError("Zero vector has no cosine direction")
    return sum(x * y for x, y in zip(a, b)) / sqrt(aa * bb)

assert abs(cosine([1, 0], [1, 0]) - 1) < 1e-12
```

This teaches the metric, not a scalable index. For production, use a tested library or service. Study exact search first, then HNSW and inverted-file approaches conceptually. Measure recall at k, latency, memory, and filtered retrieval behavior against an exact baseline.

Chunk documents by coherent meaning rather than arbitrary byte count. Store document ID, version, section, permissions, source URL, and timestamp. A change in embedding model or dimension usually requires a migration and re-embedding plan. A stale index can retrieve an old deployment instruction after the app changed.

RAG retrieves evidence and supplies it to a generator. Separate retrieval evaluation from answer evaluation. Test whether the right passage is retrieved, whether the answer is grounded, whether it cites the passage, and whether it declines unsupported claims. Retrieved text is untrusted data, not permission to execute instructions found inside it.

Compare a local PostgreSQL vector extension or other local engine with a managed service only after understanding the retrieval workload. Google's [Vector Search documentation](https://docs.cloud.google.com/vertex-ai/docs/vector-search/overview) is the current product reference; product naming can change. Managed service selection should be based on scale, filtering, operations, and cost, not the mere presence of the word AI.

**Lab:** Index 20 sanitized incident notes, define 15 questions with expected source passages, and evaluate retrieval recall. Add a malicious instruction in a document and verify the assistant treats it as content. Give the assistant no broker, shell, secret-store, or production write credentials.

## Chapter 40 Choose a certification path

There is no single Google Cloud exam that certifies Python, software architecture, data engineering, ML, security, networking, and business leadership together. Use the attachment as a menu. For your goal, begin with engineering fundamentals and Associate Cloud Engineer, then choose Cloud Developer, Cloud Architect, or DevOps according to your work. Choose Data Engineer or ML Engineer after the data chapters and additional specialization practice.

Cloud Digital Leader and Generative AI Leader are useful for broad business understanding. Generative AI Leader is not a substitute for hands-on ML engineering or proof of production RAG expertise. Google's page explicitly targets business-level knowledge and does not require hands-on technical experience. [Generative AI Leader](https://cloud.google.com/learn/certification/generative-ai-leader)

The official [Associate Cloud Engineer page](https://cloud.google.com/learn/certification/cloud-engineer) recommends at least six months of hands-on Google Cloud experience. The [Cloud Architect page](https://cloud.google.com/learn/certification/cloud-architect) recommends broader industry and Google Cloud design experience. These are experience recommendations, not evidence that finishing a reading plan creates equivalent professional experience.

Do not treat AWS and Azure certifications as exact one-to-one equivalents. Vendor products, exam domains, prerequisites, and renewal rules differ. The Weaviate, Databricks, AWS, Azure, and course credentials mentioned in your attachment are optional separate paths; this book does not claim to prepare you fully for those vendor exams. Check their official current catalogs before paying for a course or booking an exam.

### Associate Cloud Engineer preparation matrix

The currently linked standard guide groups the exam into environment setup, implementation, operations, and access/security, approximately 20%, 30%, 30%, and 20%. Do not rely on an older cached page with a different grouping. Use the current PDF linked from the certification landing page. [ACE standard guide](https://services.google.com/fh/files/misc/associate_cloud_engineer_exam_guide_english.pdf)

| Study block | Evidence to create | Extra practice beyond the trading VM |
|---|---|---|
| Environment setup | Project hierarchy, API and billing inventory | Organization policies, quotas, identity federation |
| Implementation | VM and container deployments with reviewed IaC | GKE, serverless events, alternative storage and networking |
| Operations | Restore drill, logging query, rollback report | Autoscaling, database backup tools, managed runtime operations |
| Access and security | Least-privilege service accounts and denied-access tests | Impersonation, workload identity, policy inheritance |

Your VM project does not exercise every exam product. Maintain a gap sheet for products such as Spanner, Bigtable, Firestore, AlloyDB, shared networking, managed file storage, accelerators, and AI-assisted operations. For each, explain the use case, identity model, operational responsibility, and one reason not to choose it. Reconcile your gap sheet against every objective of the official guide before booking.

### An eight week ACE revision cycle after the foundation

Week 1: resource hierarchy, projects, APIs, quota, billing, and budgets. Week 2: IAM, service accounts, impersonation, OS Login, and least privilege. Week 3: VPCs, routes, firewalls, DNS, load balancing, NAT, and private connectivity. Week 4: Compute Engine, disks, snapshots, managed instance groups, and scheduling. Week 5: containers, GKE basics, Cloud Run, and event triggers. Week 6: storage and database selection, backup, and restore. Week 7: monitoring, logs, troubleshooting, and cost investigation. Week 8: timed original practice questions, official sample questions, gap repair, and a fresh end-to-end lab without notes.

These weeks are revision blocks after practical study, not a promise that a beginner can skip experience. Mark each objective as explainable, labbed, and reviewed. Practice question percentages are your study feedback, not an official pass prediction.

## Chapter 41 Professional specialization labs

### Cloud Architect

Prepare a design for 100 isolated client accounts, then a different design for one budget-constrained client. Explain availability targets, data residency, ownership, cost, recovery objectives, and operational staffing. Compare VM isolation with shared services and tenant-scoped authorization. Design migration from the existing Redis-centered system without losing open positions. Practice current official case studies rather than memorizing old case-study names. [Cloud Architect certification](https://cloud.google.com/learn/certification/cloud-architect)

Deliver four diagrams, three architecture decision records, a cost model with dated assumptions, and a disaster-recovery plan. Defend a simpler design when it meets the requirements. You should be able to explain why more replicas can increase trading risk if account ownership is not coordinated.

### Cloud Developer

Implement clean APIs, asynchronous jobs, secure service identity, configuration injection, structured errors, integration tests, and a controlled container rollout. Exercise a managed database connection pool under concurrency and show how authentication is preserved across HTTP and WebSocket paths. Review the current [Cloud Developer objectives](https://cloud.google.com/learn/certification/cloud-developer) for products and skills your application does not cover.

### Cloud DevOps Engineer

Create a CI/CD pipeline, artifact provenance record, SLO dashboard, error-budget policy, and two incident reports. Simulate a bad release and recover without duplicate paper orders. Study fleet operations, organization setup, delivery tooling, reliability, and observability beyond a single VM. The [DevOps exam guide](https://cloud.google.com/learn/certification/guides/cloud-devops-engineer) is the scope authority.

### Cloud Security Engineer

Threat-model the admin setup page, webhook, broker callback, secret storage, CI pipeline, VM access, and backup archive. Design least privilege, incident evidence retention, key rotation, network controls, and separation of duties. Add a denied-access drill and investigate it with audit records. Follow the [Security Engineer guide](https://cloud.google.com/learn/certification/guides/cloud-security-engineer); a password-and-TOTP implementation alone is far from its full scope.

### Professional Data Engineer

Build batch and streaming ingestion of sanitized events, a governed analytical schema, lineage, late-data handling, data-quality checks, and a cost-aware dashboard. Exercise one backfill and one schema migration. Explain warehouse versus lake versus OLTP choices and how recovery preserves data fidelity. Review the [Data Engineer guide](https://cloud.google.com/learn/certification/guides/data-engineer), including platform operation and governance beyond the example pipeline.

### Professional Machine Learning Engineer

Implement reproducible training, evaluation, artifact registration, deployment, monitoring, and retraining for the advisory anomaly use case. Include responsible-AI considerations and explain why model output cannot authorize trades. Study managed ML, orchestration, scaling, and generative-AI evaluation against the current [ML Engineer certification page and linked guide](https://cloud.google.com/learn/certification/machine-learning-engineer). Coding the toy cosine function is not enough preparation for this role.

### Database and Network specializations

For [Cloud Database Engineer](https://cloud.google.com/learn/certification/cloud-database-engineer), investigate schema migration, indexing, query plans, replication, backup consistency, failover, and managed-database security. Prove a restore and compare isolation anomalies under concurrent writes.

For [Cloud Network Engineer](https://cloud.google.com/learn/certification/cloud-network-engineer), build routing and firewall diagrams, troubleshoot private connectivity, compare load balancers, and study hybrid networking and DNS. A public VM with one firewall rule is only the beginning.

Workspace administration is a different specialization around an organization's collaboration environment. It is not necessary to add Workspace administration features to your trading app. Likewise, foundational leadership certifications can be studied conceptually without pretending they are prerequisites for every engineering certification. Check the [current certification catalog](https://cloud.google.com/learn/certification) because offerings and names change.

## Chapter 42 Original practice questions with explanations

These are original study questions, not exam dumps or official exam questions. Choose the best answer under the stated constraints, then explain why the alternatives are weaker.

1. A worker times out after submitting a buy. Should it retry immediately, mark rejected, or reconcile the durable intent? **Reconcile.** The broker may have accepted it; a transport timeout does not prove failure.
2. Browser `/ws/feed` returns 426, while broker health is connected. Which path do you inspect first? **Browser-proxy-API upgrade handling.** The outbound broker socket is separate.
3. The API needs one secret. Should its VM identity receive project Owner? **No.** Grant the required secret access at the narrowest practical scope and test denial elsewhere.
4. A monthly budget alert fired. Are resources automatically stopped? **No.** An alert is not a default hard spending cap. Investigate cost and apply an approved response.
5. A query scans six months when only one day is needed. What should you inspect? **Partitioning and filters, along with the query plan and bytes processed.** A larger VM does not fix warehouse query selection.
6. A managed database replica exists. Is backup restoration unnecessary? **No.** Replication can propagate deletion or corruption; recovery requirements still need backups and restore tests.
7. Two workers pass a duplicate-position check. What is missing? **Atomic reservation or equivalent ownership control.** Two sequential-looking Python checks are not one transaction.
8. A deployment uses two live execution replicas for availability. Is this automatically safe? **No.** Account ownership, fencing where possible, broker uncertainty, and idempotent recovery must be designed.
9. A model scores well after a random split of overlapping time-series samples. What is suspect? **Temporal leakage.** Use time-aware validation and examine overlapping label windows.
10. A vector search retrieves an old runbook. Does high cosine similarity prove the instruction is current? **No.** Version, provenance, permissions, and answer grounding matter.
11. A scheduled stop leaves a reserved IP and disk. Is the bill necessarily zero? **No.** Retained resources and other services may continue charging.
12. TOTP passes but the Dhan token expired. Can the app submit a broker order? **Not on that basis.** Application authentication and broker authorization are independent.
13. A Pub/Sub consumer processes a duplicate fill message. What prevents quantity doubling? **A durable execution-identity constraint and idempotent accounting.** Queue semantics alone are insufficient.
14. Redis `PING` returns AuthenticationError. Should you increase VM RAM? **No.** Verify the runtime credential source and server authentication settings first.
15. A Cloud Run instance has an in-memory WebSocket subscriber map. Will another instance share it? **No.** Use an external communication/state design and reconnect recovery.
16. A CNC holding is absent from one portfolio response. Should you delete local ownership? **No.** Investigate settlement phase, positions, response validity, and broker evidence.
17. The code handles a malformed SDK response by setting filled quantity to requested quantity. Is that resilient? **No.** It fabricates a fill. Preserve unknown state and reconcile.
18. A client requires every operation to stay available during a partition. Can you also guarantee one globally consistent execution owner without qualification? **Not generally.** State the partition behavior and prioritize safety for order submission.
19. An engineer proposes Kubernetes for a single low-volume client. What is the first question? **Which requirement or measured constraint does it solve?** Tool complexity is a cost.
20. All unit tests pass. Are production secrets, DNS, proxy upgrade headers, and provider permissions proven correct? **No.** Those require deployment and integration evidence at their own boundaries.

For every wrong answer, identify the misunderstood concept, repeat a small lab, and explain the repaired reasoning aloud. Read every objective in your chosen official exam guide; this question set cannot certify coverage or predict an exam outcome.


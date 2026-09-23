# Container and Kubernetes teaching assets

Read Chapters 47-60 before using these files. They are a paper-only, single-replica, ephemeral SQLite demonstration. They must not be exposed publicly. They are not manifests for the real trading stack.

`paper.yaml` intentionally uses `Recreate`, one replica, and `emptyDir`. Pod replacement loses the synthetic database. This is a storage lesson, not a durability recommendation. Do not scale the replica count or apply HPA to this lab. Implement an external durable repository first.

`network-policy.yaml` is optional for a separate isolation drill and requires a CNI that enforces NetworkPolicy. Default kind networking may not enforce it. Port-forward is an operator debugging path, not proof that a NetworkPolicy allows normal Pod-to-Pod traffic.

From `docs/learning-book/lab` with Docker, kind, and kubectl installed:

```bash
docker build -t paper-school:lesson1 .
kind create cluster --name paper-school
kind load docker-image paper-school:lesson1 --name paper-school
kubectl --context kind-paper-school apply -f k8s/namespace.yaml
kubectl --context kind-paper-school apply -f k8s/paper.yaml
kubectl --context kind-paper-school -n paper-school rollout status deployment/paper-api --timeout=180s
kubectl --context kind-paper-school -n paper-school port-forward service/paper-api 8015:80
```

Open http://127.0.0.1:8015. Ctrl+C ends port-forward, not the cluster. After checking you do not need the synthetic data, `kind delete cluster --name paper-school` deletes only that local learning cluster. Container images may remain in Docker storage.

The Docker build and cluster deployments require tools not present in the authoring environment. YAML structure and learning-app tests are checked; actual image build, cluster admission, networking, and GKE deployment must be verified by running the documented labs.

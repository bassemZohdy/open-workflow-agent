# Open Workflow Agent Helm chart

This chart packages the reference Kubernetes runtime deployment as a reusable,
release-scoped chart. It defaults to the ADK image, a single `Recreate` pod,
durable data on a PVC, default-deny runtime network policy, and no external
edge or Prometheus Operator resources.

Install the ADK profile into a dedicated namespace:

```bash
helm install owa ./deploy/helm/open-workflow-agent \
  --namespace owa --create-namespace \
  --set image.repository=ghcr.io/bassemzohdy/open-workflow-agent-adk \
  --set image.tag=<immutable-tag-or-digest>
```

For LangGraph, set `engine: langgraph` and the corresponding image repository;
for OpenShift's arbitrary non-root UID, set `podSecurityContext.runAsUser` to
`null` and use the OpenShift-specific acceptance/deployment guidance.

Ingress, Gateway API `HTTPRoute`, `ServiceMonitor`, and `PrometheusRule`
resources are opt-in. Replace their example host, TLS, Gateway, and monitoring
labels with deployment-owned values before enabling them. The chart does not
create secrets, provider credentials, or a Gateway resource.

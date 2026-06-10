# nmos-cpp

Helm chart for the DMF NMOS IS-04/05 registry and mock node workloads. ADR-0025 moves these Kubernetes resources out of the Ansible runbook role so AWX catalog jobs can install the function through Helm from the cluster-internal chart catalog.

The chart defaults mirror the existing `dmf-runbooks` role: namespace `nmos`, registry and node image tags `0.1.0`, two mock nodes, port `80`, and the existing request/limit values. Override `registry.image.*`, `node.image.*`, `node.labels`, or `nmosConfig.*` when testing a different image or topology.

Install example: `helm install nmos-cpp ./charts/nmos-cpp --namespace nmos --create-namespace`.

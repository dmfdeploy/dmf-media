# dmf-media

Media-domain modules for the DMF Platform — NMOS IS-04/05 discovery, AMWA BCP compliance,
EBU LIST 2110 packet analysis, PTP topology monitoring, flow-level exporters, and the
NetBox media plugin (sender/receiver/flow schema).

## Dependencies

- `dmf-infra` — base platform (k3s, networking, storage, observability)
- `dmf-env` — environment-specific inventory

## Structure

```
roles/          — Media-domain Ansible roles (all stubs until Phase 3)
charts/         — Helm charts for media components
playbooks/      — Media deployment playbooks
docs/           — Design docs and runbooks
```

## Status

Scaffold only. Media roles are stubs. Content lands in Phase 3 of the DMF Platform Plan.

## License

This project is licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) for the full text. Upstream attribution is documented in [NOTICE](NOTICE).

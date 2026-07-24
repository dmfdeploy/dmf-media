{{- define "mxl-fabrics-demo.name" -}}
{{- .Release.Name -}}
{{- end -}}

{{- define "mxl-fabrics-demo.labels" -}}
app.kubernetes.io/name: mxl-fabrics-demo
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: mxl-fabrics-demo-{{ .Chart.Version }}
{{- end -}}

{{- define "mxl-fabrics-demo.image" -}}
{{ .Values.image.registry }}/{{ .Values.image.repository }}:{{ .Values.image.tag }}
{{- end -}}

{{/*
umbrella #258 codex round-2 (P2) — resolve a resources.<name> entry that MAY
be split per-role. info/status are single values.yaml keys rendered once
into EACH role's pod (source AND view), but measured usage differs wildly
by role (status: ~4m/17Mi source vs ~157m/56Mi view — the view side runs a
continuous GStreamer/JPEG preview pipeline the source side doesn't), so a
single shared value can no longer describe both honestly. Looks up
resources.<name>.<role> first (the per-role shape: {source: {...}, view:
{...}}); if that role key isn't present, falls back to resources.<name>
used directly as a flat {requests, limits} dict (the OLD, un-split shape —
kept working for any container name that never needs a role split, e.g. a
future shared sidecar with genuinely uniform usage).
Usage: {{ include "mxl-fabrics-demo.resourcesFor" (dict "root" $ "name" "status") }}
*/}}
{{- define "mxl-fabrics-demo.resourcesFor" -}}
{{- $entry := index .root.Values.resources .name -}}
{{- if hasKey $entry .root.Values.role -}}
{{- index $entry .root.Values.role | toYaml -}}
{{- else -}}
{{- $entry | toYaml -}}
{{- end -}}
{{- end -}}

{{- define "nmos-crosspoint.name" -}}
nmos-crosspoint
{{- end -}}

{{- define "nmos-crosspoint.fullname" -}}
{{- printf "%s" (include "nmos-crosspoint.name" .) -}}
{{- end -}}

{{- define "nmos-crosspoint.labels" -}}
app.kubernetes.io/name: {{ include "nmos-crosspoint.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end -}}

{{- define "nmos-crosspoint.nmosLabels" -}}
{{ include "nmos-crosspoint.labels" . }}
dmf.function: nmos-crosspoint
{{- end -}}

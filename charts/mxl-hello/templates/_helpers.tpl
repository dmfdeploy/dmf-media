{{- define "mxl-hello.name" -}}
mxl-hello
{{- end -}}

{{- define "mxl-hello.fullname" -}}
{{- printf "%s" (include "mxl-hello.name" .) -}}
{{- end -}}

{{- define "mxl-hello.labels" -}}
app.kubernetes.io/name: {{ include "mxl-hello.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end -}}

{{- define "mxl-hello.podLabels" -}}
{{ include "mxl-hello.labels" . }}
dmf.function: mxl-hello
{{- end -}}

{{- define "nmos-cpp.name" -}}
nmos-cpp
{{- end -}}

{{- define "nmos-cpp.fullname" -}}
{{- printf "%s" (include "nmos-cpp.name" .) -}}
{{- end -}}

{{- define "nmos-cpp.labels" -}}
app.kubernetes.io/name: {{ include "nmos-cpp.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end -}}

{{- define "nmos-cpp.nmosLabels" -}}
{{ include "nmos-cpp.labels" . }}
dmf.function: nmos-cpp
{{- end -}}

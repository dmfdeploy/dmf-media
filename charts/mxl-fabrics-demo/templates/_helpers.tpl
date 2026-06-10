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

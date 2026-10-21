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

{{/*
The controller image, by digest only (dmfdeploy/dmfdeploy#629). A tag, an
empty digest or a short one fails the render, so a tag can never reach a
deployment. A legacy image.tag value fails too, rather than being ignored.
*/}}
{{- define "nmos-crosspoint.image" -}}
{{- if hasKey .Values.image "tag" -}}
{{- fail "image.tag is not supported: nmos-crosspoint references its image by digest only (set image.digest)" -}}
{{- end -}}
{{- if not (regexMatch "^sha256:[0-9a-f]{64}$" (.Values.image.digest | default "")) -}}
{{- fail (printf "image.digest must be sha256:<64 hex digits>, got %q" (.Values.image.digest | default "")) -}}
{{- end -}}
{{- if or (contains "@" .Values.image.repository) (contains ":" (last (splitList "/" .Values.image.repository))) -}}
{{- fail "image.repository must not carry a digest or tag; set image.digest" -}}
{{- end -}}
{{- printf "%s@%s" .Values.image.repository .Values.image.digest -}}
{{- end -}}

{{/* Expand the chart name. */}}
{{- define "open-workflow-agent.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/* Create a release-scoped full name. */}}
{{- define "open-workflow-agent.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name (include "open-workflow-agent.name" .) | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{/* Common labels. */}}
{{- define "open-workflow-agent.labels" -}}
helm.sh/chart: {{ include "open-workflow-agent.chart" . }}
{{ include "open-workflow-agent.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/engine: {{ .Values.engine | quote }}
{{- end }}

{{/* Selector labels. */}}
{{- define "open-workflow-agent.selectorLabels" -}}
app.kubernetes.io/name: {{ include "open-workflow-agent.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/* Chart label. */}}
{{- define "open-workflow-agent.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

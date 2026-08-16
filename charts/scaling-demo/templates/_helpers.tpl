{{/*
One ScaledObject shape for every workload in this chart. KEDA owns replica
counts exclusively — no Deployment in this chart sets `replicas`, because Helm
would reset it on every upgrade and fight the HPA.

Args (dict):
  name  workload + ScaledObject name (also the scaleTargetRef)
  ns    release namespace
  s     the workload's .scaling values block
  kind  "temporal" | "traefik"
  temporal-only: endpoint, tns (temporal namespace), queue
  traefik-only:  prom (prometheus address), port
*/}}
{{- define "scalingdemo.scaledobject" -}}
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: {{ .name }}
  namespace: {{ .ns }}
spec:
  scaleTargetRef:
    name: {{ .name }}
  pollingInterval: {{ .s.pollingInterval }}
  cooldownPeriod: {{ .s.cooldownPeriod }}
  minReplicaCount: {{ .s.minReplicas }}
  maxReplicaCount: {{ .s.maxReplicas }}
  advanced:
    horizontalPodAutoscalerConfig:
      behavior:
        scaleDown:
          stabilizationWindowSeconds: 60
  triggers:
  {{- if eq .kind "temporal" }}
    - type: temporal
      metadata:
        endpoint: {{ .endpoint | quote }}
        namespace: {{ .tns | quote }}
        taskQueue: {{ .queue | quote }}
        targetQueueSize: {{ .s.targetQueueSize | quote }}
        activationTargetQueueSize: {{ .s.activationTargetQueueSize | quote }}
        # `nexus` is not a valid value — Temporal exposes backlog only for
        # these two, which is why Nexus handlers cannot scale from zero.
        queueTypes: "workflow,activity"
  {{- else }}
    - type: prometheus
      metadata:
        serverAddress: {{ .prom | quote }}
        threshold: {{ .s.requestsPerSecond | quote }}
        # Requests/sec measured by Traefik at the ingress — no app
        # instrumentation. Traefik labels backends <ns>-<service>-<port>.
        query: >-
          sum(rate(traefik_service_requests_total{service="{{ .ns }}-{{ .name }}-{{ .port }}@kubernetes"}[1m]))
        ignoreNullValues: "true"
  {{- end }}
{{- end -}}

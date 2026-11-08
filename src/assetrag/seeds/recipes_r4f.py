"""Round-4 recipes, part F: Kubernetes / Helm / Kustomize / Prometheus configs (structural tests with PyYAML) and Python gap-fillers
(JSON Lines + XML, property-based and snapshot testing helpers, natural-language dates, safe subprocess pipelines)."""
from .recipes import RC, UT
from .recipes_cfg import PYT, STRUCT

K8S_ASSUME = STRUCT + ["not applied to a cluster and not validated by kubectl/helm/kustomize/promtool (none in the sandbox): run `kubectl apply --dry-run=server`, `helm lint` / `helm template`, `kubectl kustomize` and `promtool check rules` in CI"]

R4F = [
RC("cfg-k8s-deployment-hardened-probes-limits", "Kubernetes Deployment done right: probes, resource requests/limits, non-root hardening, PDB, HPA, NetworkPolicy, graceful shutdown", "yaml",
   "Ship a web service to Kubernetes that rolls out without dropping requests, survives node drains, cannot run as root, and does not starve or get starved.",
   "One multi-document manifest: ServiceAccount (token not auto-mounted), Deployment (RollingUpdate maxUnavailable 0, pinned image, startup+readiness+liveness probes on a named port with distinct /readyz and /healthz, requests<=limits, runAsNonRoot, read-only root FS, no privilege escalation, drop ALL capabilities, seccomp RuntimeDefault, preStop sleep inside terminationGracePeriodSeconds, topology spread), Service, PodDisruptionBudget, HPA v2 and an ingress-only NetworkPolicy. Python tests parse every document and assert cross-references (selectors, port names, quantities).",
   {"app.yaml": r'''apiVersion: v1
kind: ServiceAccount
metadata:
  name: web
  labels: {app.kubernetes.io/name: web}
automountServiceAccountToken: false
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
  labels: {app.kubernetes.io/name: web}
spec:
  replicas: 3
  revisionHistoryLimit: 3
  selector:
    matchLabels: {app.kubernetes.io/name: web}
  strategy:
    type: RollingUpdate
    rollingUpdate: {maxSurge: 1, maxUnavailable: 0}
  template:
    metadata:
      labels: {app.kubernetes.io/name: web}
    spec:
      serviceAccountName: web
      automountServiceAccountToken: false
      terminationGracePeriodSeconds: 30
      securityContext:
        runAsNonRoot: true
        runAsUser: 10001
        runAsGroup: 10001
        fsGroup: 10001
        seccompProfile: {type: RuntimeDefault}
      topologySpreadConstraints:
        - maxSkew: 1
          topologyKey: kubernetes.io/hostname
          whenUnsatisfiable: ScheduleAnyway
          labelSelector:
            matchLabels: {app.kubernetes.io/name: web}
      containers:
        - name: web
          image: registry.example.com/team/web:1.4.2
          imagePullPolicy: IfNotPresent
          ports:
            - {name: http, containerPort: 8080}
          env:
            - name: DATABASE_URL
              valueFrom:
                secretKeyRef: {name: web-db, key: url}
          resources:
            requests: {cpu: 100m, memory: 128Mi}
            limits: {memory: 256Mi}
          startupProbe:
            httpGet: {path: /healthz, port: http}
            periodSeconds: 2
            failureThreshold: 30
          readinessProbe:
            httpGet: {path: /readyz, port: http}
            periodSeconds: 5
            timeoutSeconds: 2
            failureThreshold: 2
          livenessProbe:
            httpGet: {path: /healthz, port: http}
            periodSeconds: 10
            timeoutSeconds: 2
            failureThreshold: 3
          lifecycle:
            preStop:
              exec: {command: ["sleep", "10"]}
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities: {drop: ["ALL"]}
          volumeMounts:
            - {name: tmp, mountPath: /tmp}
      volumes:
        - name: tmp
          emptyDir: {sizeLimit: 64Mi}
---
apiVersion: v1
kind: Service
metadata:
  name: web
  labels: {app.kubernetes.io/name: web}
spec:
  selector: {app.kubernetes.io/name: web}
  ports:
    - {name: http, port: 80, targetPort: http}
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: web
spec:
  maxUnavailable: 1
  selector:
    matchLabels: {app.kubernetes.io/name: web}
---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: web
spec:
  scaleTargetRef: {apiVersion: apps/v1, kind: Deployment, name: web}
  minReplicas: 3
  maxReplicas: 10
  metrics:
    - type: Resource
      resource:
        name: cpu
        target: {type: Utilization, averageUtilization: 70}
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: web-ingress-only
spec:
  podSelector:
    matchLabels: {app.kubernetes.io/name: web}
  policyTypes: [Ingress]
  ingress:
    - from:
        - namespaceSelector:
            matchLabels: {kubernetes.io/metadata.name: ingress-nginx}
      ports:
        - {protocol: TCP, port: 8080}
''', "test_app.py": r'''import pathlib, re, unittest
import yaml

DOCS = list(yaml.safe_load_all(pathlib.Path("app.yaml").read_text()))
BY = {d["kind"]: d for d in DOCS}
DEP = BY["Deployment"]; POD = DEP["spec"]["template"]["spec"]; C = POD["containers"][0]
LABELS = DEP["spec"]["template"]["metadata"]["labels"]
UNITS = {"m": 1e-3, "Ki": 1024, "Mi": 1024 ** 2, "Gi": 1024 ** 3, "": 1}


def qty(s):
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(m|Ki|Mi|Gi)?", str(s)); assert m, s
    return float(m.group(1)) * UNITS[m.group(2) or ""]


class Basics(unittest.TestCase):
    def test_every_document_is_well_formed(self):
        for d in DOCS: self.assertTrue(d["apiVersion"] and d["kind"] and d["metadata"]["name"], d)

    def test_selectors_line_up(self):
        sel = DEP["spec"]["selector"]["matchLabels"]; self.assertEqual({k: LABELS[k] for k in sel}, sel)                # immutable once deployed: must match pod labels
        self.assertEqual({k: LABELS[k] for k in BY["Service"]["spec"]["selector"]}, BY["Service"]["spec"]["selector"])
        for kind, path in (("PodDisruptionBudget", ("spec", "selector", "matchLabels")), ("NetworkPolicy", ("spec", "podSelector", "matchLabels"))):
            m = BY[kind]
            for p in path: m = m[p]
            self.assertTrue(set(m.items()) <= set(LABELS.items()), kind)
        self.assertTrue(set(POD["topologySpreadConstraints"][0]["labelSelector"]["matchLabels"].items()) <= set(LABELS.items()))

    def test_service_targets_a_named_container_port(self):
        names = {p["name"] for p in C["ports"]}; self.assertIn(BY["Service"]["spec"]["ports"][0]["targetPort"], names)

    def test_image_is_pinned(self):
        self.assertNotRegex(C["image"], r":latest$"); self.assertRegex(C["image"], r":[\w.-]+$|@sha256:[0-9a-f]{64}$")


class Rollout(unittest.TestCase):
    def test_zero_downtime_strategy(self):
        s = DEP["spec"]["strategy"]; self.assertEqual((s["type"], s["rollingUpdate"]["maxUnavailable"]), ("RollingUpdate", 0)); self.assertGreaterEqual(DEP["spec"]["replicas"], 2)

    def test_probes(self):
        names = {p["name"] for p in C["ports"]}
        for k in ("startupProbe", "readinessProbe", "livenessProbe"):
            self.assertIn(C[k]["httpGet"]["port"], names, k)
        self.assertNotEqual(C["readinessProbe"]["httpGet"]["path"], C["livenessProbe"]["httpGet"]["path"])          # liveness must not depend on downstream services
        self.assertNotIn("initialDelaySeconds", C["livenessProbe"])                                                 # startupProbe replaces guesswork delays
        st = C["startupProbe"]; self.assertGreaterEqual(st["periodSeconds"] * st["failureThreshold"], 30)

    def test_graceful_shutdown_budget(self):
        sleep = int(C["lifecycle"]["preStop"]["exec"]["command"][1]); self.assertGreater(POD["terminationGracePeriodSeconds"], sleep + 5)

    def test_pdb_and_hpa(self):
        pdb = BY["PodDisruptionBudget"]["spec"]; self.assertTrue(("minAvailable" in pdb) != ("maxUnavailable" in pdb))
        h = BY["HorizontalPodAutoscaler"]["spec"]; self.assertEqual(h["scaleTargetRef"]["name"], DEP["metadata"]["name"]); self.assertGreaterEqual(h["minReplicas"], 2); self.assertGreater(h["maxReplicas"], h["minReplicas"])
        self.assertLessEqual(h["metrics"][0]["resource"]["target"]["averageUtilization"], 80)


class Resources(unittest.TestCase):
    def test_requests_and_limits(self):
        r = C["resources"]; self.assertIn("memory", r["requests"]); self.assertIn("cpu", r["requests"]); self.assertIn("memory", r["limits"])
        for k in r["limits"]: self.assertLessEqual(qty(r["requests"][k]), qty(r["limits"][k]), k)
        self.assertNotIn("cpu", r["limits"])             # CPU limits throttle bursts; keep a request, skip the limit unless you have a reason


class Hardening(unittest.TestCase):
    def test_container_security(self):
        s = C["securityContext"]; self.assertIs(s["allowPrivilegeEscalation"], False); self.assertIs(s["readOnlyRootFilesystem"], True); self.assertEqual(s["capabilities"]["drop"], ["ALL"])

    def test_pod_security(self):
        s = POD["securityContext"]; self.assertIs(s["runAsNonRoot"], True); self.assertGreater(s["runAsUser"], 0); self.assertEqual(s["seccompProfile"]["type"], "RuntimeDefault")

    def test_service_account_token_not_mounted(self):
        self.assertIs(POD["automountServiceAccountToken"], False); self.assertEqual(POD["serviceAccountName"], BY["ServiceAccount"]["metadata"]["name"])

    def test_secrets_come_from_secret_refs_not_literals(self):
        for e in C["env"]:
            if re.search(r"(?i)pass|secret|token|key|url", e["name"]): self.assertIn("valueFrom", e, e["name"])

    def test_writable_paths_are_explicit_and_bounded(self):
        self.assertTrue(all("sizeLimit" in v["emptyDir"] for v in POD["volumes"]))

    def test_network_policy_is_ingress_only_from_one_namespace(self):
        np = BY["NetworkPolicy"]["spec"]; self.assertEqual(np["policyTypes"], ["Ingress"]); self.assertEqual(len(np["ingress"]), 1)
        self.assertEqual(np["ingress"][0]["ports"][0]["port"], C["ports"][0]["containerPort"])


if __name__ == "__main__": unittest.main()
'''}, PYT, assumptions=K8S_ASSUME + ["Kubernetes >= 1.25 (policy/v1 PDB, autoscaling/v2, seccompProfile field)", "No CPU limit is deliberate (avoids CFS throttling); add one if your cluster policy requires it"], compat={"kubernetes": ">=1.25"},
   tags=["kubernetes", "k8s", "deployment", "probes", "liveness", "readiness", "startup-probe", "resource-limits", "requests", "security-context", "non-root", "pdb", "hpa", "networkpolicy", "rolling-update", "graceful-shutdown", "yaml", "manifest"],
   avoid="image: latest; liveness probes that call the database; no resource requests; runAsRoot; maxUnavailable>0 with 1-2 replicas; secrets in env literals; mutable selectors changed after first deploy."),

RC("cfg-helm-chart-values-templates", "Helm chart skeleton: Chart.yaml, values.yaml with schema, helpers and a Deployment template; tests catch .Values typos and unbalanced blocks", "yaml",
   "Package an app as a Helm chart whose values are documented, validated, and not silently misspelled in templates.",
   "Chart.yaml (apiVersion v2, semver version, appVersion), a values.yaml whose structure mirrors what templates use, values.schema.json so `helm install` rejects bad input, _helpers.tpl with name/fullname/labels helpers, and a Deployment template using `default .Chart.AppVersion` for the image tag, `toYaml | nindent` blocks and `with` guards. Python tests statically check the three failure modes that bite most often: a `.Values.x.y` path that does not exist, mismatched `{{ if/with/range }}`...`{{ end }}`, and schema/values drift.",
   {"Chart.yaml": r'''apiVersion: v2
name: web
description: A Helm chart for the web service
type: application
version: 0.3.0
appVersion: "1.4.2"
''', "values.yaml": r'''replicaCount: 2

image:
  repository: registry.example.com/team/web
  tag: ""            # defaults to .Chart.AppVersion
  pullPolicy: IfNotPresent

service:
  type: ClusterIP
  port: 80

containerPort: 8080

resources:
  requests:
    cpu: 100m
    memory: 128Mi
  limits:
    memory: 256Mi

env: {}

nodeSelector: {}
tolerations: []
''', "values.schema.json": r'''{
  "$schema": "https://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["image"],
  "properties": {
    "replicaCount": {"type": "integer", "minimum": 1},
    "image": {
      "type": "object",
      "required": ["repository"],
      "properties": {
        "repository": {"type": "string", "minLength": 1},
        "tag": {"type": "string"},
        "pullPolicy": {"enum": ["Always", "IfNotPresent", "Never"]}
      }
    },
    "service": {"type": "object", "properties": {"type": {"enum": ["ClusterIP", "NodePort", "LoadBalancer"]}, "port": {"type": "integer", "minimum": 1, "maximum": 65535}}},
    "containerPort": {"type": "integer", "minimum": 1, "maximum": 65535},
    "resources": {"type": "object"},
    "env": {"type": "object", "additionalProperties": {"type": "string"}},
    "nodeSelector": {"type": "object"},
    "tolerations": {"type": "array"}
  }
}
''', "templates/_helpers.tpl": r'''{{- define "web.name" -}}
{{- .Chart.Name | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "web.fullname" -}}
{{- if contains .Chart.Name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{- define "web.selectorLabels" -}}
app.kubernetes.io/name: {{ include "web.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "web.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{ include "web.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}
''', "templates/deployment.yaml": r'''apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include "web.fullname" . }}
  labels:
    {{- include "web.labels" . | nindent 4 }}
spec:
  replicas: {{ .Values.replicaCount }}
  selector:
    matchLabels:
      {{- include "web.selectorLabels" . | nindent 6 }}
  template:
    metadata:
      labels:
        {{- include "web.selectorLabels" . | nindent 8 }}
    spec:
      containers:
        - name: {{ .Chart.Name }}
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag | default .Chart.AppVersion }}"
          imagePullPolicy: {{ .Values.image.pullPolicy }}
          ports:
            - name: http
              containerPort: {{ .Values.containerPort }}
          {{- with .Values.env }}
          env:
            {{- range $k, $v := . }}
            - name: {{ $k }}
              value: {{ $v | quote }}
            {{- end }}
          {{- end }}
          resources:
            {{- toYaml .Values.resources | nindent 12 }}
      {{- with .Values.nodeSelector }}
      nodeSelector:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with .Values.tolerations }}
      tolerations:
        {{- toYaml . | nindent 8 }}
      {{- end }}
''', "templates/service.yaml": r'''apiVersion: v1
kind: Service
metadata:
  name: {{ include "web.fullname" . }}
  labels:
    {{- include "web.labels" . | nindent 4 }}
spec:
  type: {{ .Values.service.type }}
  ports:
    - port: {{ .Values.service.port }}
      targetPort: http
      name: http
  selector:
    {{- include "web.selectorLabels" . | nindent 4 }}
''', "test_chart.py": r'''import json, pathlib, re, unittest
import yaml

ROOT = pathlib.Path(".")
CHART = yaml.safe_load((ROOT / "Chart.yaml").read_text())
VALUES = yaml.safe_load((ROOT / "values.yaml").read_text())
SCHEMA = json.loads((ROOT / "values.schema.json").read_text())
TEMPLATES = {p.name: p.read_text() for p in (ROOT / "templates").iterdir()}
ALL = "\n".join(TEMPLATES.values())


def has_path(tree, path):
    for part in path:
        if not isinstance(tree, dict) or part not in tree: return False
        tree = tree[part]
    return True


class ChartMeta(unittest.TestCase):
    def test_required_fields(self):
        self.assertEqual(CHART["apiVersion"], "v2"); self.assertRegex(CHART["version"], r"^\d+\.\d+\.\d+(-[\w.]+)?$"); self.assertTrue(CHART["appVersion"]); self.assertRegex(CHART["name"], r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")


class Values(unittest.TestCase):
    def test_every_values_reference_exists(self):
        refs = set(re.findall(r"\.Values\.([A-Za-z0-9_.]+)", ALL)); self.assertTrue(refs)
        for ref in refs: self.assertTrue(has_path(VALUES, ref.split(".")), "templates use .Values.%s but values.yaml does not define it" % ref)

    def test_unused_top_level_values_are_flagged(self):
        used = {r.split(".")[0] for r in re.findall(r"\.Values\.([A-Za-z0-9_.]+)", ALL)}
        self.assertEqual(set(VALUES) - used, set(), "values.yaml keys no template reads (dead config)")

    def test_schema_covers_values_and_types_agree(self):
        props = SCHEMA["properties"]; self.assertEqual(set(VALUES) - set(props), set())
        kinds = {int: "integer", str: "string", dict: "object", list: "array", bool: "boolean", float: "number"}
        for k, v in VALUES.items():
            if "type" in props[k]: self.assertEqual(props[k]["type"], kinds[type(v)], k)
        self.assertTrue(set(SCHEMA["required"]) <= set(VALUES))

    def test_defaults_satisfy_enums_and_ranges(self):
        self.assertIn(VALUES["image"]["pullPolicy"], SCHEMA["properties"]["image"]["properties"]["pullPolicy"]["enum"]); self.assertGreaterEqual(VALUES["replicaCount"], 1)

    def test_no_latest_default_tag(self):
        self.assertNotEqual(VALUES["image"]["tag"], "latest")


class Templates(unittest.TestCase):
    def test_blocks_are_balanced(self):
        for name, text in TEMPLATES.items():
            opens = len(re.findall(r"{{-?\s*(?:if|with|range|define|block)\b", text)); ends = len(re.findall(r"{{-?\s*end\s*-?}}", text))
            self.assertEqual(opens, ends, name); self.assertEqual(text.count("{{"), text.count("}}"), name)

    def test_included_helpers_are_defined(self):
        defined = set(re.findall(r'define\s+"([^"]+)"', ALL))
        for used in re.findall(r'include\s+"([^"]+)"', ALL): self.assertIn(used, defined, used)

    def test_image_tag_falls_back_to_app_version(self):
        self.assertRegex(TEMPLATES["deployment.yaml"], r"\.Values\.image\.tag \| default \.Chart\.AppVersion")

    def test_selector_labels_are_stable_across_upgrades(self):
        sel = re.search(r'define "web.selectorLabels"(.*?){{- end }}', ALL, re.S).group(1)
        self.assertNotIn("version", sel.lower()); self.assertNotIn("Chart.Version", sel)             # selectors are immutable: never put versions in them

    def test_optional_blocks_are_guarded_and_user_strings_quoted(self):
        t = TEMPLATES["deployment.yaml"]; self.assertIn("with .Values.nodeSelector", t); self.assertIn("with .Values.tolerations", t); self.assertIn("| quote", t)

    def test_toyaml_uses_nindent_not_indent(self):
        for m in re.findall(r"toYaml[^}]*}}", ALL): self.assertIn("nindent", m)


if __name__ == "__main__": unittest.main()
'''}, PYT, assumptions=K8S_ASSUME + ["Helm 3. Template rendering is NOT executed here: the static checks catch typos and structure, `helm lint` and `helm template | kubectl apply --dry-run` remain mandatory"], compat={"helm": ">=3.8"},
   tags=["helm", "chart", "helm-chart", "values", "values.yaml", "values-schema", "templates", "helpers", "tpl", "kubernetes", "k8s", "nindent", "toyaml", "release", "packaging", "yaml"],
   avoid="Changing selectorLabels between releases; `indent` with toYaml (use nindent); reading .Values keys that values.yaml never defines; versions in selector labels; tag: latest as a default."),

RC("cfg-kustomize-base-overlays", "Kustomize: shared base plus dev/prod overlays with image tags, replica patches, namespaces and name prefixes", "yaml",
   "Run the same app in several environments, changing only what differs (replicas, image tag, namespace, config) without copy-pasted manifests.",
   "A base (deployment, service, kustomization) and overlays that reference `../../base`, set `namespace`, `namePrefix`, `images[].newTag`, and strategic-merge/JSON6902 `patches` with explicit `target`. Uses `labels` with includeSelectors: false instead of the deprecated `commonLabels` (which rewrites immutable selectors). Python tests resolve file references, check overlay hygiene, and apply a small merge to prove dev and prod end up with the intended replicas and tags.",
   {"base/kustomization.yaml": r'''apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - deployment.yaml
  - service.yaml
labels:
  - pairs:
      app.kubernetes.io/part-of: shop
    includeSelectors: false
''', "base/deployment.yaml": r'''apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
spec:
  replicas: 1
  selector:
    matchLabels: {app.kubernetes.io/name: web}
  template:
    metadata:
      labels: {app.kubernetes.io/name: web}
    spec:
      containers:
        - name: web
          image: registry.example.com/team/web:1.0.0
          ports: [{name: http, containerPort: 8080}]
          resources:
            requests: {cpu: 50m, memory: 64Mi}
''', "base/service.yaml": r'''apiVersion: v1
kind: Service
metadata:
  name: web
spec:
  selector: {app.kubernetes.io/name: web}
  ports: [{name: http, port: 80, targetPort: http}]
''', "overlays/dev/kustomization.yaml": r'''apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: shop-dev
namePrefix: dev-
resources:
  - ../../base
images:
  - name: registry.example.com/team/web
    newTag: 1.5.0-rc1
''', "overlays/prod/kustomization.yaml": r'''apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: shop-prod
resources:
  - ../../base
images:
  - name: registry.example.com/team/web
    newTag: 1.4.2
patches:
  - path: replicas-and-resources.yaml
    target: {kind: Deployment, name: web}
  - target: {kind: Service, name: web}
    patch: |-
      - op: add
        path: /metadata/annotations
        value: {service.beta.kubernetes.io/aws-load-balancer-type: nlb}
''', "overlays/prod/replicas-and-resources.yaml": r'''apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
spec:
  replicas: 4
  template:
    spec:
      containers:
        - name: web
          resources:
            requests: {cpu: 500m, memory: 512Mi}
            limits: {memory: 512Mi}
''', "test_overlays.py": r'''import copy, pathlib, unittest
import yaml

ROOT = pathlib.Path(".")
load = lambda p: yaml.safe_load((ROOT / p).read_text())
BASE_DEP = load("base/deployment.yaml")


def merge(a, b):
    """Strategic-merge-ish: dicts merge, lists of named objects merge by name, everything else is replaced."""
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for k, v in b.items(): out[k] = merge(a[k], v) if k in a else v
        return out
    if isinstance(a, list) and isinstance(b, list) and all(isinstance(x, dict) and "name" in x for x in a + b):
        by = {x["name"]: x for x in a}
        for x in b: by[x["name"]] = merge(by[x["name"]], x) if x["name"] in by else x
        return list(by.values())
    return b


def build(overlay):
    k = load("overlays/%s/kustomization.yaml" % overlay); dep = copy.deepcopy(BASE_DEP)
    for p in k.get("patches", []):
        if "path" in p: dep = merge(dep, load("overlays/%s/%s" % (overlay, p["path"])))
    c = dep["spec"]["template"]["spec"]["containers"][0]
    for img in k.get("images", []):
        repo, _, _ = c["image"].rpartition(":")
        if repo == img["name"]: c["image"] = "%s:%s" % (repo, img["newTag"])
    return k, dep


class Files(unittest.TestCase):
    def test_every_referenced_file_exists(self):
        for kfile in ROOT.glob("**/kustomization.yaml"):
            k = yaml.safe_load(kfile.read_text())
            for r in k.get("resources", []): self.assertTrue((kfile.parent / r).exists(), "%s -> %s" % (kfile, r))
            for p in k.get("patches", []):
                if "path" in p: self.assertTrue((kfile.parent / p["path"]).exists(), p["path"])

    def test_overlays_build_on_the_base_and_stay_in_their_own_namespace(self):
        spaces = set()
        for env in ("dev", "prod"):
            k = load("overlays/%s/kustomization.yaml" % env); self.assertIn("../../base", k["resources"]); self.assertTrue(k["namespace"].endswith(env)); spaces.add(k["namespace"])
        self.assertEqual(len(spaces), 2)

    def test_no_deprecated_commonlabels_which_rewrite_immutable_selectors(self):
        for kfile in ROOT.glob("**/kustomization.yaml"):
            k = yaml.safe_load(kfile.read_text()); self.assertNotIn("commonLabels", k, kfile)
            for lab in k.get("labels", []): self.assertIs(lab.get("includeSelectors", False), False, kfile)

    def test_patches_name_their_target(self):
        for p in load("overlays/prod/kustomization.yaml")["patches"]: self.assertIn("target", p)


class Result(unittest.TestCase):
    def test_prod_scales_up_and_gets_more_resources_than_dev(self):
        _, dev = build("dev"); _, prod = build("prod")
        self.assertEqual((dev["spec"]["replicas"], prod["spec"]["replicas"]), (1, 4))
        cd, cp = dev["spec"]["template"]["spec"]["containers"][0], prod["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual(cp["resources"]["requests"]["memory"], "512Mi"); self.assertEqual(cd["resources"]["requests"]["memory"], "64Mi")
        self.assertEqual(cp["ports"], cd["ports"])                              # a patch must not drop fields it did not mention

    def test_image_tags_are_overridden_per_environment_and_never_latest(self):
        _, dev = build("dev"); _, prod = build("prod")
        self.assertEqual(dev["spec"]["template"]["spec"]["containers"][0]["image"].rsplit(":", 1)[1], "1.5.0-rc1")
        self.assertEqual(prod["spec"]["template"]["spec"]["containers"][0]["image"].rsplit(":", 1)[1], "1.4.2")
        for env in ("dev", "prod"):
            for i in load("overlays/%s/kustomization.yaml" % env)["images"]: self.assertNotEqual(i["newTag"], "latest")

    def test_patch_targets_exist_in_the_base(self):
        names = {(load("base/%s.yaml" % f)["kind"], load("base/%s.yaml" % f)["metadata"]["name"]) for f in ("deployment", "service")}
        for p in load("overlays/prod/kustomization.yaml")["patches"]: self.assertIn((p["target"]["kind"], p["target"]["name"]), names)

    def test_base_alone_is_deployable_and_minimal(self):
        self.assertEqual(BASE_DEP["spec"]["replicas"], 1); self.assertNotIn("namespace", BASE_DEP["metadata"])


if __name__ == "__main__": unittest.main()
'''}, PYT, assumptions=K8S_ASSUME + ["The test's mini-merge approximates strategic merge for this shape only; `kubectl kustomize overlays/prod` is the real check", "kustomize >= 5 (`labels` with includeSelectors, inline `patch`)"], compat={"kustomize": ">=5.0"},
   tags=["kustomize", "overlays", "kubernetes", "k8s", "environments", "dev-prod", "patches", "strategic-merge", "json6902", "images", "newtag", "namespace", "nameprefix", "gitops", "base", "yaml", "configuration"],
   avoid="commonLabels on an existing deployment (selector is immutable); patches without a target; latest tags in overlays; copying whole manifests per environment."),

RC("cfg-prometheus-alert-rules-alertmanager", "Prometheus alerting: recording rules, symptom-based alerts (down, error ratio, latency, multi-window SLO burn), Alertmanager routing and inhibition", "yaml",
   "Get paged for user-visible problems, not for every CPU blip, with alerts that carry a runbook and don't flap.",
   "A rules file with recording rules named level:metric:operation, alerts with `for` durations, severity labels, summary/description/runbook_url annotations, ratio-based error rate, histogram_quantile latency, and a multi-window multi-burn-rate SLO alert (14.4x over 1h AND 5m for a 99.9% SLO); an alertmanager.yml that groups, waits, repeats sanely, routes by severity and inhibits warnings while a critical fires. Python tests parse the YAML and lint the PromQL shape (balanced brackets, rate() on counters, windows vs scrape interval).",
   {"rules.yml": r'''groups:
  - name: web-recording
    interval: 30s
    rules:
      - record: job:http_requests:rate5m
        expr: sum by (job) (rate(http_requests_total[5m]))
      - record: job:http_errors:rate5m
        expr: sum by (job) (rate(http_requests_total{status=~"5.."}[5m]))
      - record: job:http_error_ratio:rate5m
        expr: job:http_errors:rate5m / job:http_requests:rate5m
      - record: job:http_error_ratio:rate1h
        expr: sum by (job) (rate(http_requests_total{status=~"5.."}[1h])) / sum by (job) (rate(http_requests_total[1h]))

  - name: web-alerts
    rules:
      - alert: InstanceDown
        expr: up == 0
        for: 3m
        labels: {severity: critical}
        annotations:
          summary: "{{ $labels.instance }} of job {{ $labels.job }} is down"
          description: "Scrape target has been unreachable for more than 3 minutes."
          runbook_url: https://runbooks.example.com/instance-down

      - alert: HighErrorRatio
        expr: job:http_error_ratio:rate5m > 0.05
        for: 10m
        labels: {severity: warning}
        annotations:
          summary: "{{ $labels.job }}: {{ $value | humanizePercentage }} of requests fail"
          description: "5xx ratio above 5% for 10 minutes."
          runbook_url: https://runbooks.example.com/high-error-ratio

      - alert: SLOBurnRateFast
        expr: job:http_error_ratio:rate1h > (14.4 * 0.001) and job:http_error_ratio:rate5m > (14.4 * 0.001)
        for: 2m
        labels: {severity: critical}
        annotations:
          summary: "{{ $labels.job }} burns its 99.9% error budget 14x too fast"
          description: "Both the 1h and 5m windows exceed 14.4x the allowed error rate; at this pace the 30-day budget is gone in ~2 days."
          runbook_url: https://runbooks.example.com/slo-burn

      - alert: HighLatencyP99
        expr: histogram_quantile(0.99, sum by (le, job) (rate(http_request_duration_seconds_bucket[5m]))) > 1.5
        for: 15m
        labels: {severity: warning}
        annotations:
          summary: "{{ $labels.job }} p99 latency is {{ $value | humanizeDuration }}"
          description: "99th percentile above 1.5s for 15 minutes."
          runbook_url: https://runbooks.example.com/latency

      - alert: DiskWillFillIn24h
        expr: predict_linear(node_filesystem_avail_bytes{fstype!~"tmpfs|overlay"}[6h], 24 * 3600) < 0
        for: 1h
        labels: {severity: warning}
        annotations:
          summary: "{{ $labels.mountpoint }} on {{ $labels.instance }} fills within 24h"
          description: "Linear extrapolation of the last 6 hours says free space reaches zero inside a day."
          runbook_url: https://runbooks.example.com/disk-full
''', "alertmanager.yml": r'''global:
  resolve_timeout: 5m

route:
  receiver: team-chat
  group_by: [alertname, job]
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h
  routes:
    - matchers: [severity="critical"]
      receiver: oncall-pager
      group_wait: 10s
      repeat_interval: 1h

receivers:
  - name: team-chat
    slack_configs:
      - channel: "#alerts"
        send_resolved: true
        api_url_file: /etc/alertmanager/secrets/slack_url
  - name: oncall-pager
    pagerduty_configs:
      - routing_key_file: /etc/alertmanager/secrets/pagerduty_key

inhibit_rules:
  - source_matchers: [severity="critical"]
    target_matchers: [severity="warning"]
    equal: [alertname, job]
''', "test_rules.py": r'''import pathlib, re, unittest
import yaml

R = yaml.safe_load(pathlib.Path("rules.yml").read_text())
AM = yaml.safe_load(pathlib.Path("alertmanager.yml").read_text())
RULES = [r for g in R["groups"] for r in g["rules"]]
ALERTS = [r for r in RULES if "alert" in r]
RECORDS = [r for r in RULES if "record" in r]
SCRAPE_INTERVAL = 15       # seconds; keep in sync with prometheus.yml
SEVERITIES = {"critical", "warning", "info"}


def seconds(d):
    m = re.fullmatch(r"(\d+)(s|m|h|d)", d); assert m, d
    return int(m.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]


def balanced(expr):
    stack = []
    for ch in re.sub(r'"[^"]*"', "", expr):
        if ch in "([{": stack.append(ch)
        elif ch in ")]}":
            if not stack or "([{"[")]}".index(ch)] != stack.pop(): return False
    return not stack


class Shape(unittest.TestCase):
    def test_alerts_have_everything_a_responder_needs(self):
        self.assertGreaterEqual(len(ALERTS), 4)
        for a in ALERTS:
            self.assertRegex(a["alert"], r"^[A-Z][A-Za-z0-9]+$"); self.assertIn(a["labels"]["severity"], SEVERITIES, a["alert"])
            for k in ("summary", "description", "runbook_url"): self.assertTrue(a["annotations"].get(k), "%s lacks %s" % (a["alert"], k))
            self.assertTrue(a["annotations"]["runbook_url"].startswith("https://"))

    def test_alert_names_are_unique(self):
        names = [a["alert"] for a in ALERTS]; self.assertEqual(len(names), len(set(names)))

    def test_every_alert_waits_before_firing(self):
        for a in ALERTS: self.assertGreaterEqual(seconds(a["for"]), 120, a["alert"])          # `for` is what stops flapping pages

    def test_pages_are_for_symptoms(self):
        for a in ALERTS:
            if a["labels"]["severity"] == "critical": self.assertRegex(a["expr"], r"up\b|burn|error_ratio|_ratio", a["alert"])           # critical = users hurt or target gone, not "CPU high"


class Promql(unittest.TestCase):
    def test_expressions_have_balanced_brackets(self):
        for r in RULES: self.assertTrue(balanced(r["expr"]), r)

    def test_counters_are_wrapped_in_rate(self):
        for r in RULES:
            raw = len(re.findall(r"\b\w+_total\b", r["expr"])); wrapped = len(re.findall(r"\b(?:rate|irate|increase)\(\s*\w+_total\b", r["expr"]))
            self.assertEqual(raw, wrapped, "a counter is used without rate()/increase(): %s" % r["expr"])

    def test_rate_windows_cover_at_least_four_scrapes(self):
        for r in RULES:
            for w in re.findall(r"rate\([^)]*\[(\d+[smhd])\]", r["expr"]): self.assertGreaterEqual(seconds(w), 4 * SCRAPE_INTERVAL, r["expr"])

    def test_error_alerts_use_ratios_not_absolute_counts(self):
        a = next(x for x in ALERTS if x["alert"] == "HighErrorRatio"); self.assertRegex(a["expr"], r"ratio"); self.assertRegex(a["expr"], r">\s*0\.\d+")

    def test_latency_uses_histogram_quantile_with_le_preserved(self):
        a = next(x for x in ALERTS if x["alert"] == "HighLatencyP99"); self.assertIn("histogram_quantile(0.99", a["expr"]); self.assertRegex(a["expr"], r"by \(le[,)]| by \(.*\ble\b")

    def test_slo_burn_alert_checks_long_and_short_windows(self):
        a = next(x for x in ALERTS if x["alert"] == "SLOBurnRateFast"); e = a["expr"]
        self.assertIn(" and ", e); self.assertIn("rate1h", e); self.assertIn("rate5m", e); self.assertRegex(e, r"14\.4 \* 0\.001")        # 99.9% SLO => budget 0.001


class Recording(unittest.TestCase):
    def test_names_follow_level_metric_operation(self):
        for r in RECORDS: self.assertRegex(r["record"], r"^[a-z_]+:[a-z_]+:[a-z0-9_]+$", r["record"])

    def test_alerts_only_use_recorded_series_that_exist(self):
        recorded = {r["record"] for r in RECORDS}
        for a in ALERTS:
            for name in re.findall(r"\b([a-z_]+:[a-z_]+:[a-z0-9_]+)\b", a["expr"]): self.assertIn(name, recorded, a["alert"])


class Routing(unittest.TestCase):
    def receivers(self): return {r["name"] for r in AM["receivers"]}

    def test_routes_point_at_defined_receivers(self):
        names = self.receivers(); self.assertIn(AM["route"]["receiver"], names)
        for sub in AM["route"]["routes"]: self.assertIn(sub["receiver"], names)

    def test_grouping_and_timers_prevent_storms(self):
        r = AM["route"]; self.assertTrue(r["group_by"]); self.assertGreaterEqual(seconds(r["group_wait"]), 10); self.assertGreaterEqual(seconds(r["repeat_interval"]), 3600)

    def test_every_severity_label_in_rules_is_routed_somewhere(self):
        routed = {m.split('"')[1] for s in AM["route"]["routes"] for m in s["matchers"] if m.startswith("severity=")}
        self.assertIn("critical", routed)

    def test_critical_inhibits_warning_of_the_same_alert(self):
        i = AM["inhibit_rules"][0]; self.assertEqual(i["source_matchers"], ['severity="critical"']); self.assertEqual(i["target_matchers"], ['severity="warning"']); self.assertIn("alertname", i["equal"])

    def test_secrets_are_read_from_files_not_inlined(self):
        text = pathlib.Path("alertmanager.yml").read_text()
        self.assertNotRegex(text, r"hooks\.slack\.com/services/|routing_key:\s*\w{20,}|api_url:\s*https"); self.assertIn("_file:", text)


if __name__ == "__main__": unittest.main()
'''}, PYT, assumptions=K8S_ASSUME + ["Scrape interval assumed 15s; adjust SCRAPE_INTERVAL and rate windows together", "Metric names (http_requests_total, http_request_duration_seconds_bucket) are the common client-library conventions; rename to match your instrumentation", "SLO numbers assume 99.9% over 30 days; pair the fast-burn alert with a slow-burn (6x over 6h/30m) ticket alert for full coverage"], compat={"prometheus": ">=2.40", "alertmanager": ">=0.26"},
   tags=["prometheus", "alerting", "alert-rules", "alertmanager", "recording-rules", "slo", "burn-rate", "error-ratio", "latency", "histogram_quantile", "promql", "runbook", "oncall", "monitoring", "observability", "inhibit", "routing", "yaml"],
   avoid="Alerting on causes (CPU > 80%) instead of symptoms; alerts with no `for`; rate() windows shorter than 4 scrapes; raw counters in expressions; one giant route that pages for warnings; inline webhook secrets."),
]

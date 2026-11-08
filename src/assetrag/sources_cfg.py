"""Config / devops template sources (gitignore, CI workflows, compose stacks, release config).

Same gate as every other source: the repo's LICENSE text is fingerprinted (CC0/MIT only here), files are scanned
for secrets and malware, and each file must parse in its own format (`config` check). Everything here is human-written.
Not included: Arch PKGBUILDs - the official package repos live on gitlab.archlinux.org, and the gate only clones
github.com repos (and the GitHub mirrors carry no LICENSE file to fingerprint).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .ingest import RepoCtx, Spec

GH = "https://github.com/"


def _gitignore(ctx: RepoCtx, rel: str, files: dict[str, bytes]):
    name = Path(rel).name.removesuffix(".gitignore")
    scope = "editor/OS" if rel.startswith("Global/") else "project"
    return (f"gitignore template for {name} ({scope}): ignore rules for build output, caches, dependencies and local files. "
            f".gitignore starter for {name} repositories.", [name.lower(), "gitignore", "git"])


def _workflow(ctx: RepoCtx, rel: str, files: dict[str, bytes]):
    p = ctx.path / Path(rel).parent / "properties" / (Path(rel).stem + ".properties.json")
    name, desc, cats = Path(rel).stem, "", []
    try:
        m = json.loads(p.read_text())
        name, desc, cats = m.get("name", name), m.get("description", ""), m.get("categories") or []
    except (OSError, ValueError):
        pass
    kind = Path(rel).parts[0]
    return (f"GitHub Actions workflow ({kind}): {name}. {desc} Starter workflow YAML for .github/workflows.".strip(),
            [w.lower() for w in cats if w] + ["github-actions", "workflow", "ci", kind])


def _compose(ctx: RepoCtx, rel: str, files: dict[str, bytes]):
    readme = next((v.decode("utf-8", "ignore") for k, v in files.items() if k.lower() == "readme.md"), "")
    first = ""
    for para in re.split(r"\n\s*\n", re.sub(r"```.*?```", "", readme, flags=re.S)):
        t = re.sub(r"\s+", " ", re.sub(r"[#>*_`]|\[([^\]]*)\]\([^)]*\)", lambda m: m.group(1) or "", para)).strip()
        if len(t) > 40 and not t.lower().startswith(("project structure", "deploy with")):
            first = t[:240]; break
    n = Path(rel).name
    return (f"Docker Compose stack: {n.replace('-', ' ')}. {first} compose.yaml with Dockerfile for local development and deployment.",
            [w for w in re.split(r"[-_]", n) if len(w) > 1] + ["docker", "compose", "dockerfile", "container"])


def _release(label: str):
    def d(ctx: RepoCtx, rel: str, files: dict[str, bytes]):
        return (f"{label}: {Path(rel).name} configuration for building and releasing Go binaries (archives, checksums, "
                f"cross-platform builds, changelog).", ["goreleaser", "release", "go", "ci", "cross-platform"])
    return d


SPECS: list[tuple[str, Spec]] = [
    ("github/gitignore", Spec(id="gitignore", repo=GH + "github/gitignore", category="template", kind="gitignore",
                              include=["*.gitignore"], exclude=["community/*", "*/*/*.gitignore"], checks=["config"], languages=[],
                              tags=["gitignore", "git", "config"], prefix="gitignore: ", min_bytes=20, describe=_gitignore, difficulty=1)),
    ("actions/starter-workflows", Spec(id="starter-workflows", repo=GH + "actions/starter-workflows", category="template", kind="ci-workflow",
                                       include=["ci/*.yml", "deployments/*.yml", "automation/*.yml", "code-scanning/*.yml"],
                                       exclude=["*/properties/*"], checks=["config"], languages=["yaml"],
                                       tags=["github-actions", "workflow", "ci", "yaml"], prefix="GitHub Actions: ",
                                       min_bytes=150, describe=_workflow, difficulty=1)),
    ("docker/awesome-compose", Spec(id="awesome-compose", repo=GH + "docker/awesome-compose", category="template", kind="compose-stack",
                                    include=["*"], exclude=["*/*", "docs", "_*"], unit="dir", checks=["config"], languages=["yaml"],
                                    tags=["docker", "compose", "dockerfile", "container"], prefix="Compose stack: ",
                                    min_bytes=100, describe=_compose, difficulty=2)),
    ("goreleaser/example", Spec(id="goreleaser-example", repo=GH + "goreleaser/example", category="template", kind="release-config",
                                include=[".goreleaser.yaml"], checks=["config"], languages=["yaml"], tags=["goreleaser", "release", "go"],
                                prefix="GoReleaser example: ", min_bytes=100, describe=_release("GoReleaser example"), difficulty=1)),
    ("goreleaser/goreleaser", Spec(id="goreleaser-own", repo=GH + "goreleaser/goreleaser", category="template", kind="release-config",
                                   include=[".goreleaser.yaml"], checks=["config"], languages=["yaml"], tags=["goreleaser", "release", "go"],
                                   prefix="GoReleaser (project's own release config): ", min_bytes=100,
                                   describe=_release("GoReleaser's own release config"), difficulty=2)),
]

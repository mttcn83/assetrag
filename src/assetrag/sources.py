"""Curated source catalog. Each entry says *where* assets live; legality and
quality are decided by the ingest engine, never by this file."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .ingest import RepoCtx, Spec, default_describe, humanize

GH = "https://github.com/"


def icon_describe(label: str):
    def d(ctx: RepoCtx, rel: str, files: dict[str, bytes]):
        name = Path(rel).stem
        tags: list[str] = [w for w in re.split(r"[-_]", name) if len(w) > 1]
        extra = ""
        meta = ctx.path / Path(rel).with_suffix(".json")
        if meta.exists():
            try:
                m = json.loads(meta.read_text())
                tags += m.get("tags", []) + m.get("categories", [])
                extra = " Related: " + ", ".join(m.get("tags", [])[:6]) if m.get("tags") else ""
            except ValueError:
                pass
        return (f"{label} SVG icon '{name.replace('-', ' ')}'. Scalable vector icon for buttons, menus and toolbars.{extra}",
                sorted(set(tags)))
    return d


def icons(sid, repo, label, glob, stride, cap=100000, exclude=()):
    return (repo, Spec(id=sid, repo=GH + repo, category="ui", kind="icon", include=[glob], exclude=list(exclude), checks=["svg"],
                       languages=["svg"], tags=["icon", "svg", label.lower()], stride=stride, cap=cap,
                       min_bytes=60, describe=icon_describe(label), prefix=f"{label} icon: ", difficulty=1))


def html_snippets(sid, repo, cat, kind, glob, label, tags, fw=("tailwindcss",), cap=10000, stride=1, checks=("html",)):
    def nm(ctx, rel):
        parts = Path(rel).parts
        return f"{label} {parts[-2].replace('-', ' ')} #{Path(rel).stem}"
    return (repo, Spec(id=sid, repo=GH + repo, category=cat, kind=kind, include=[glob], unit="file",
                       checks=list(checks), frameworks=list(fw), languages=["html"], tags=list(tags),
                       npm_deps=["tailwindcss"] if "tailwindcss" in fw else [], name_fn=nm, cap=cap, stride=stride,
                       min_bytes=200, difficulty=1))


def pages(sid, repo, include, label, tags, fw=("bootstrap",), exclude=(), cap=60, root="", cat="template",
          npm=("bootstrap",)):
    EX = ["*node_modules*", "*/docs/*", "docs/*", "*vendor*", "*/test*", "*partials*", "*/documentation/*", "*/demo/*",
          "*/_includes/*", "*/_layouts/*", "*.min.html", "*/src/*/_*", "*/plugins/*", "*/libs/*", "*/lib/*"]
    return (repo, Spec(id=sid, repo=GH + repo, category=cat, kind="page-template", include=list(include),
                       exclude=EX + list(exclude), unit="html", checks=["html", "htmldoc"], frameworks=list(fw),
                       languages=["html", "css", "javascript"], tags=list(tags) + ["template"], cap=cap, root=root,
                       npm_deps=list(npm), prefix=f"{label}: ", min_bytes=800, difficulty=2))


SPECS: list[tuple[str, Spec]] = [
    # ------------------------------------------------------------- icons (ui)
    icons("lucide", "lucide-icons/lucide", "Lucide", "icons/*.svg", 1),
    icons("tabler-icons", "tabler/tabler-icons", "Tabler", "icons/outline/*.svg", 2),
    icons("heroicons", "tailwindlabs/heroicons", "Heroicons", "src/24/outline/*.svg", 1),
    icons("phosphor", "phosphor-icons/core", "Phosphor", "assets/regular/*.svg", 1),
    icons("bootstrap-icons", "twbs/icons", "Bootstrap", "icons/*.svg", 1),
    icons("feather", "feathericons/feather", "Feather", "icons/*.svg", 1),
    icons("ionicons", "ionic-team/ionicons", "Ionicons", "src/svg/*.svg", 1, exclude=["*-outline.svg", "*-sharp.svg"]),
    icons("iconoir", "iconoir-icons/iconoir", "Iconoir", "icons/regular/*.svg", 1),
    icons("eva", "akveo/eva-icons", "Eva", "package/icons/outline/svg/*.svg", 1),
    icons("radix", "radix-ui/icons", "Radix", "packages/radix-icons/icons/*.svg", 1),
    icons("octicons", "primer/octicons", "Octicons", "icons/*-24.svg", 1),
    icons("carbon", "carbon-design-system/carbon-icons", "Carbon", "src/svg/32/*.svg", 1),
    icons("simple-icons", "simple-icons/simple-icons", "Simple Icons brand logo", "icons/*.svg", 2),
    # ------------------------------------------------- UI blocks / components
    html_snippets("hyperui-app", "markmead/hyperui", "ui", "component", "public/examples/application/**/*.html",
                  "HyperUI", ["tailwind", "component"]),
    ("shadcn-ui/ui", Spec(id="shadcn", repo=GH + "shadcn-ui/ui", category="ui", kind="component",
                          include=["apps/v4/registry/new-york-v4/ui/*.tsx"], checks=["ts"],
                          frameworks=["react", "tailwindcss"], languages=["typescript"], tags=["component", "shadcn", "radix"],
                          support=["apps/v4/lib/utils.ts"], prefix="shadcn/ui ", difficulty=2, min_bytes=300)),
    # ------------------------------------------------------------- templates
    html_snippets("hyperui-marketing", "markmead/hyperui", "template", "section-template",
                  "public/examples/marketing/**/*.html", "HyperUI marketing", ["tailwind", "marketing", "section", "landing"]),
    html_snippets("hyperui-neobrutalism", "markmead/hyperui", "template", "section-template",
                  "public/examples/neobrutalism/**/*.html", "HyperUI neobrutalism", ["tailwind", "neobrutalism", "section"]),
    html_snippets("hyperui-templates", "markmead/hyperui", "template", "page-template",
                  "public/examples/templates/**/*.html", "HyperUI page", ["tailwind", "page", "full-page"]),
    pages("adminlte", "ColorlibHQ/AdminLTE", ["dist/**/*.html"], "AdminLTE", ["admin", "dashboard"], cap=70),
    pages("sbadmin2", "startbootstrap/startbootstrap-sb-admin-2", ["*.html"], "SB Admin 2", ["admin", "dashboard"], cap=30),
    pages("sbadmin", "startbootstrap/startbootstrap-sb-admin", ["dist/*.html"], "SB Admin", ["admin", "dashboard"], cap=30),
    *[pages(f"sb-{n}", f"startbootstrap/startbootstrap-{n}", ["dist/*.html", "*.html"], f"Start Bootstrap {n.replace('-', ' ')}",
            ["website", n.split("-")[0]], cap=10, exclude=["*/node_modules/*"]) for n in (
        "agency", "clean-blog", "creative", "freelancer", "grayscale", "landing-page", "modern-business", "new-age",
        "resume", "stylish-portfolio", "coming-soon", "one-page-wonder", "small-business", "shop-homepage",
        "business-casual", "heroic-features", "simple-sidebar", "scrolling-nav")],
    pages("material-dashboard", "creativetimofficial/material-dashboard", ["pages/*.html", "*.html"], "Material Dashboard", ["admin", "dashboard", "material"], cap=30),
    pages("argon-dashboard", "creativetimofficial/argon-dashboard", ["pages/*.html", "*.html"], "Argon Dashboard", ["admin", "dashboard"], cap=30),
    pages("soft-ui-dashboard", "creativetimofficial/soft-ui-dashboard", ["pages/*.html", "*.html"], "Soft UI Dashboard", ["admin", "dashboard"], cap=30),
    pages("material-kit", "creativetimofficial/material-kit", ["pages/*.html", "presentation.html", "*.html"], "Material Kit", ["website", "ui-kit"], cap=30),
    pages("now-ui-kit", "creativetimofficial/now-ui-kit", ["*.html", "examples/*.html"], "Now UI Kit", ["website", "ui-kit"], cap=30),
    pages("paper-kit-2", "creativetimofficial/paper-kit-2", ["*.html", "examples/*.html"], "Paper Kit 2", ["website", "ui-kit"], cap=30),
    pages("argon-design-system", "creativetimofficial/argon-design-system", ["*.html", "pages/*.html", "examples/*.html"], "Argon Design System", ["website", "ui-kit"], cap=30),
    pages("sufee", "puikinsh/sufee-admin-dashboard", ["*.html"], "Sufee Admin", ["admin", "dashboard"], cap=60),
    pages("purple-admin", "BootstrapDash/PurpleAdmin-Free-Admin-Template", ["**/*.html"], "Purple Admin", ["admin", "dashboard"], cap=60),
    pages("star-admin", "BootstrapDash/StarAdmin-Free-Bootstrap-Admin-Template", ["**/*.html"], "Star Admin", ["admin", "dashboard"], cap=60),
    pages("sneat", "themeselection/sneat-bootstrap-html-admin-template-free", ["**/*.html"], "Sneat", ["admin", "dashboard"], cap=60),
    ("mertJF/tailblocks", Spec(id="tailblocks", repo=GH + "mertJF/tailblocks", category="template", kind="section-template",
                               include=["src/blocks/*/light/*.js"], checks=["ts"], frameworks=["react", "tailwindcss"],
                               languages=["javascript"], tags=["tailwind", "section", "landing", "react"], npm_deps=["tailwindcss"],
                               prefix="Tailblocks ", min_bytes=500, difficulty=1)),
    pages("preline-templates", "htmlstreamofficial/preline", ["templates/**/*.html"], "Preline", ["tailwind", "website"], fw=("tailwindcss", "preline"), npm=("tailwindcss", "preline"), cap=10),
    pages("materio", "ThemeSelection/materio-bootstrap-html-admin-template-free", ["html/*.html"], "Materio", ["admin", "dashboard"], cap=60),
    pages("argon-tailwind", "creativetimofficial/argon-dashboard-tailwind", ["build/pages/*.html", "build/*.html"], "Argon Tailwind", ["admin", "dashboard", "tailwind"], fw=("tailwindcss",), npm=("tailwindcss",), cap=20),
    pages("tailwindtoolbox-landing", "tailwindtoolbox/Landing-Page", ["index.html"], "Tailwind Toolbox landing", ["landing", "tailwind"], fw=("tailwindcss",), npm=("tailwindcss",), cap=2),
    pages("tailwindtoolbox-admin", "tailwindtoolbox/Admin-Template", ["index.html"], "Tailwind Toolbox admin", ["admin", "dashboard", "tailwind"], fw=("tailwindcss",), npm=("tailwindcss",), cap=2),
    # ------------------------------------------------------ algorithms
    ("TheAlgorithms/Python", Spec(id="algos-py", repo=GH + "TheAlgorithms/Python", category="algorithm", kind="algorithm",
                                  include=["*/*.py", "*/*/*.py"], exclude=["*/__init__.py", "scripts/*", "*test*", "project_euler/*", "web_programming/*", "machine_learning/*", "computer_vision/*", "neural_network/*", "quantum/*", "fuzzy_logic/*", "audio_filters/*", "financial/*", "knapsack/tests/*", "docs/*", "*/tests/*"],
                                  checks=["py", "pyimports", "doctest"], languages=["python"], tags=["algorithm", "python"],
                                  cap=260, stride=3, min_bytes=500, difficulty=1)),
    ("TheAlgorithms/Go", Spec(id="algos-go", repo=GH + "TheAlgorithms/Go", category="algorithm", kind="algorithm",
                              include=["*/*.go"], exclude=["*_test.go", "*/*/*", "project_euler/*", "structure/*", "cmd/*"],
                              checks=["gofmt", "gotest"], languages=["go"], tags=["algorithm", "go"], pair_tests=True,
                              cap=120, stride=2, min_bytes=400, difficulty=1)),
    # --------------------------------------------------------- CLI / TUI
    ("Textualize/textual", Spec(id="textual-examples", repo=GH + "Textualize/textual", category="cli", kind="tui-example",
                                include=["docs/examples/**/*.py"], exclude=["*__init__*"], siblings=[".tcss"],
                                checks=["py", "pyimports"], languages=["python"], frameworks=["textual"],
                                tags=["tui", "textual", "python"], allow_imports={"textual": "textual", "rich": "rich"},
                                cap=150, stride=2, min_bytes=500, prefix="Textual: ", difficulty=2)),
    ("Textualize/rich", Spec(id="rich-examples", repo=GH + "Textualize/rich", category="cli", kind="cli-example",
                             include=["examples/*.py"], checks=["py", "pyimports"], languages=["python"], frameworks=["rich"],
                             tags=["cli", "terminal", "rich", "python"], allow_imports={"rich": "rich"}, cap=40,
                             min_bytes=400, prefix="Rich: ", difficulty=1)),
    ("fastapi/typer", Spec(id="typer-examples", repo=GH + "fastapi/typer", category="cli", kind="cli-template",
                           include=["docs_src/**/*.py"], exclude=["*__init__*"], checks=["py", "pyimports"],
                           languages=["python"], frameworks=["typer"], tags=["cli", "typer", "python"],
                           allow_imports={"typer": "typer", "rich": "rich"}, cap=60, stride=4, min_bytes=500,
                           prefix="Typer: ", difficulty=1)),
    ("pallets/click", Spec(id="click-examples", repo=GH + "pallets/click", category="cli", kind="cli-template",
                           include=["examples/*"], unit="dir", checks=["py", "pyimports"], languages=["python"],
                           frameworks=["click"], tags=["cli", "click", "python"],
                           allow_imports={"click": "click", "PIL": "pillow", "colorama": "colorama"}, cap=20,
                           prefix="Click: ", min_bytes=300, difficulty=2)),
    ("charmbracelet/bubbletea", Spec(id="bubbletea-examples", repo=GH + "charmbracelet/bubbletea", category="cli", kind="tui-example",
                                     include=["examples/*"], exclude=["examples/go.*", "examples/README*"], unit="dir",
                                     checks=["gofmt"], languages=["go"], frameworks=["bubbletea"],
                                     tags=["tui", "bubbletea", "go"], cap=70, prefix="Bubble Tea: ", min_bytes=500,
                                     difficulty=2)),
    ("ratatui/ratatui", Spec(id="ratatui-apps", repo=GH + "ratatui/ratatui", category="cli", kind="tui-example",
                             include=["examples/apps/*"], unit="dir", checks=["rustfmt"], languages=["rust"],
                             frameworks=["ratatui"], tags=["tui", "ratatui", "rust"], cap=40, prefix="Ratatui: ",
                             min_bytes=500, difficulty=3)),
    # ------------------------------------------------------- animations
    ("jonsuh/hamburgers", Spec(id="hamburgers", repo=GH + "jonsuh/hamburgers", category="animation", kind="css-animation",
                               include=["_sass/hamburgers/types/*.scss"], checks=["css"], languages=["scss"],
                               tags=["animation", "hamburger", "menu", "css", "transition"],
                               support=["_sass/hamburgers/_base.scss"], prefix="Hamburger menu animation: ", min_bytes=200,
                               difficulty=1)),
    ("magicuidesign/magicui", Spec(id="magicui", repo=GH + "magicuidesign/magicui", category="animation", kind="animated-component",
                                   include=["apps/www/registry/magicui/*.tsx"], checks=["ts"], frameworks=["react", "tailwindcss"],
                                   languages=["typescript"], tags=["animation", "motion", "react", "component"],
                                   prefix="Magic UI ", min_bytes=300, difficulty=2)),
    ("ibelick/motion-primitives", Spec(id="motion-primitives", repo=GH + "ibelick/motion-primitives", category="animation", kind="animated-component",
                                       include=["components/core/*.tsx"], checks=["ts"], frameworks=["react", "tailwindcss"],
                                       languages=["typescript"], tags=["animation", "motion", "react", "component"],
                                       prefix="Motion Primitives ", license_path="LICENCE.md", min_bytes=300, difficulty=2)),
    # ---------------------------------------------------------- backend
    ("mahmoud/boltons", Spec(id="boltons", repo=GH + "mahmoud/boltons", category="backend", kind="module",
                             include=["boltons/*.py"], exclude=["boltons/__init__.py"], tree=True, tests=["tests/test_{stem}.py"],
                             checks=["py", "pytest"], languages=["python"], tags=["utility", "python", "stdlib-extension"],
                             prefix="boltons.", min_bytes=800, difficulty=1)),
    # ---------------------------------------------- more animations / illustrations
    ("codse/animata", Spec(id="animata", repo=GH + "codse/animata", category="animation", kind="animated-component",
                           include=["animata/*/*.tsx"], exclude=["*.stories.tsx"], siblings=[".css"], checks=["ts"],
                           frameworks=["react", "tailwindcss"], languages=["typescript"],
                           tags=["animation", "motion", "react", "tailwind", "component"], prefix="Animata ", min_bytes=300, difficulty=2)),
    ("jh3y/whirl", Spec(id="whirl", repo=GH + "jh3y/whirl", category="animation", kind="css-animation",
                        include=["src/whirls/*.scss"], checks=["css"], languages=["scss"],
                        tags=["animation", "loader", "spinner", "css", "loading"], prefix="CSS loader animation: ", min_bytes=150, difficulty=1)),
    ("Martz90/vivify", Spec(id="vivify", repo=GH + "Martz90/vivify", category="animation", kind="css-animation-library",
                            include=["vivify.css"], checks=["css"], languages=["css"],
                            tags=["animation", "css", "keyframes", "transitions", "entrance", "exit"], prefix="Vivify CSS animations: ",
                            min_bytes=1000, max_bytes=900_000, difficulty=1)),
    ("cyberalien/line-md", Spec(id="line-md", repo=GH + "cyberalien/line-md", category="animation", kind="animated-icon",
                                include=["svg/*.svg"], checks=["svg"], languages=["svg"], stride=5, cap=250,
                                tags=["animation", "animated-icon", "svg", "smil", "icon"], prefix="Animated SVG icon: ", min_bytes=150, difficulty=1)),
    ("MariaLetta/free-gophers-pack", Spec(id="free-gophers", repo=GH + "MariaLetta/free-gophers-pack", category="ui", kind="illustration",
                                          include=["*/svg/*.svg", "*/*/svg/*.svg"], exclude=["examples/*", "preview/*"], checks=["svg"],
                                          languages=["svg"], tags=["illustration", "svg", "gopher", "character", "cc0"],
                                          prefix="Gopher illustration: ", min_bytes=500, max_bytes=900_000, difficulty=1)),
]


def by_repo() -> dict[str, list[Spec]]:
    from .sources_cfg import SPECS as CFG
    out: dict[str, list[Spec]] = {}
    for repo, spec in SPECS + CFG:
        out.setdefault(GH + repo if not repo.startswith("http") else repo, []).append(spec)
    return out

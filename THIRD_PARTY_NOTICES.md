# Third-party notices

AssetRAG itself is Apache-2.0. It depends on, but does not bundle, the packages below (installed by pip/uv from PyPI under their own licences;
versions as tested on 2026-11-08). Licence identifiers are the ones each package declares in its metadata.

## Runtime dependencies
| Package | Licence | Role |
|---|---|---|
| qdrant-client | Apache-2.0 | embedded vector store |
| mcp (+ pydantic, starlette, uvicorn, anyio, httpx, ...) | MIT / BSD-3-Clause | MCP server |
| httpx | BSD-3-Clause | embedding client |
| PyYAML | MIT | config and YAML parsing |
| numpy, grpcio, protobuf (via qdrant-client) | BSD-3-Clause / Apache-2.0 | vector maths, transport |
| certifi (via httpx) | MPL-2.0 | CA bundle, installed unmodified |

## Optional (`pip install "assetrag[repomap]"`)
tree-sitter and the tree-sitter-{javascript,typescript,go,rust,java,c,bash} grammars: MIT.

## Development only
pytest: MIT.

## Code and data derived from other projects
- **gitleaks rules** (`src/assetrag/data/gitleaks_rules.json`): Copyright (c) 2019 Zachary Rice, MIT License, from
  https://github.com/gitleaks/gitleaks (config/gitleaks.toml at commit 5f6ec56). Regexes were translated from Go RE2 to Python `re`.
  MIT notice: "Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files
  (the "Software"), to deal in the Software without restriction ... The above copyright notice and this permission notice shall be included in
  all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND."
- **Ideas** (no code copied): Aider's repository-map concept (Apache-2.0).

## Things you provide that have their own terms
- **Embedding model weights** (for example `embeddinggemma`) are not included. If you download one, its licence (for Gemma models, Google's
  Gemma Terms of Use) applies to you. `embed_mode=off` runs AssetRAG with no model at all.
- **Tools it calls if present**: git, bubblewrap, ClamAV (GPL-2.0, run as a separate program, never linked or bundled), gh (MIT).
- **Library content** you ingest stays under each source's licence. `import_asset` writes the required notice text into your project's
  `THIRD_PARTY_NOTICES.md`.

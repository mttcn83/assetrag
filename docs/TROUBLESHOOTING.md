# Troubleshooting

| Symptom | Fix |
|---|---|
| `embedding server … failed` | Start Unsloth Studio, load an embedding model, check `embed_url`/`embed_key`; `assetrag doctor` |
| `library busy` / "already accessed by another instance" | Embedded Qdrant allows one process. Stop `populate`/other CLI runs, or point Qdrant at a server |
| Search returns nothing for a framework | Framework filter hides incompatible assets; drop `--framework` to see agnostic ones |
| `import` refused | `assetrag license <id> --profile …` shows the exact reason (copyleft/dependency/notice) |
| `bubblewrap` errors | needs unprivileged user namespaces (`sysctl kernel.unprivileged_userns_clone=1` on hardened kernels) |
| ClamAV `ERROR` | `sudo freshclam`, ensure `clamscan --version` works; the gate blocks release on errors |
| Changed embedding model | automatic: vector store rebuilds; run `assetrag reindex` |
| Go checks fail offline | module cache is prefetched with `go mod download` into `data/gopath`; ensure network on first ingest |

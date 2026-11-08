"""'Use this instead of that': a short list of well-known command-line tools that agents mistake for libraries."""
PREFER = {
    "burntsushi/ripgrep": "ripgrep is an application. As a library, use the `ignore` crate (gitignore-aware walking, same author) with `grep-searcher`/`regex`; for a plain walk, `walkdir`.",
    "sharkdp/fd": "fd is an application. For file discovery inside a Rust program use the `ignore` or `walkdir` crates; to call fd itself, run it as a subprocess with an argument list.",
    "sharkdp/bat": "bat is an application. For syntax highlighting in Rust use `syntect`; for a pager-style CLI call bat as a subprocess.",
    "junegunn/fzf": "fzf is an application. Inside a Go program use a fuzzy-matching package (e.g. sahilm/fuzzy); to give users an fzf-style picker, run fzf as a subprocess.",
}


def note_for(repo: str) -> str:
    return PREFER.get((repo or "").lower(), "")

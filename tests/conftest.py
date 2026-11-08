import os
import pytest
from assetrag.config import Config
from assetrag.library import Library, Candidate

MIT = """MIT License

Copyright (c) 2024 Someone

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND."""

NC = """Creative Commons Attribution-NonCommercial 4.0 International Public License. You may not use the material for commercial purposes. NonCommercial means not primarily intended for or directed towards commercial advantage."""


@pytest.fixture
def lib(tmp_path):
    cfg = Config(home=tmp_path / "home").ensure()
    L = Library(cfg)
    yield L
    L.close()


def cand(**kw):
    base = dict(name="demo", category="backend", kind="module", description="demo util",
                source_url="https://github.com/o/r/blob/abc/x.py", repo="o/r", commit_sha="abc",
                files={"x.py": b"def add(a, b):\n    return a + b\n" * 3}, license_text=MIT,
                license_url="https://github.com/o/r/blob/abc/LICENSE", declared_license="MIT",
                checks={"python-ast-parse": "pass"})
    base.update(kw)
    return Candidate(**base)

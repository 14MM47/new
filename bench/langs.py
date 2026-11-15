"""The four baseline languages: how a program is named, checked strictly, built and run."""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass

HERE = os.path.dirname(os.path.abspath(__file__))
TS_CACHE = os.path.join(HERE, ".cache", "ts")


@dataclass
class Lang:
    key: str
    title: str
    source: str
    rules: str            # what the model is told about the toolchain

    def build(self, build_dir: str) -> tuple:
        """Strict check and build. Returns (ok, compiler output, run command)."""
        raise NotImplementedError


def _sh(cmd: list, cwd: str, timeout: int = 180) -> tuple:
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return p.returncode == 0, (p.stdout + p.stderr).strip()
    except subprocess.TimeoutExpired:
        return False, "the build did not finish in time"


class Python(Lang):
    def build(self, d):
        ok, out = _sh(["mypy", "--strict", "--no-color-output", "--no-error-summary", "--cache-dir=/dev/null", "main.py"], d)
        return ok, out, [shutil.which("python3"), "-I", os.path.join(d, "main.py")]


class TypeScript(Lang):
    def build(self, d):
        link = os.path.join(d, "node_modules")
        if not os.path.exists(link):
            os.symlink(os.path.join(TS_CACHE, "node_modules"), link)
        ok, out = _sh([os.path.join(TS_CACHE, "node_modules", ".bin", "tsc"), "--strict", "--target", "es2022",
                       "--module", "commonjs", "--types", "node", "--noUncheckedIndexedAccess", "--pretty", "false",
                       "--outDir", "out", "main.ts"], d)
        return ok, out, [shutil.which("node"), os.path.join(d, "out", "main.js")]


class Rust(Lang):
    def build(self, d):
        ok, out = _sh(["rustc", "--edition", "2021", "--color", "never", "-o", "main", "main.rs"], d)
        return ok, out if not ok else "", [os.path.join(d, "main")]


class Go(Lang):
    def build(self, d):
        env_fix = ["env", "GOFLAGS=-mod=mod", "GO111MODULE=off", "GOCACHE=" + os.path.join(HERE, ".cache", "go")]
        ok, out = _sh(env_fix + ["go", "vet", "main.go"], d)
        if ok:
            ok, out = _sh(env_fix + ["go", "build", "-o", "main", "main.go"], d)
        return ok, out, [os.path.join(d, "main")]


LANGS = {
    "python": Python("python", "Python", "main.py",
                     "Python 3.13, standard library only, one file. It must pass `mypy --strict`, so annotate every function."),
    "typescript": TypeScript("typescript", "TypeScript", "main.ts",
                             "TypeScript for Node.js 22, built-in `node:` modules only, one file. It is compiled with "
                             "`tsc --strict --noUncheckedIndexedAccess` as CommonJS, so use `import ... from \"node:fs\"` style imports."),
    "rust": Rust("rust", "Rust", "main.rs", "Rust 2021 edition, standard library only (no crates), one file compiled with `rustc`."),
    "go": Go("go", "Go", "main.go", "Go 1.24, standard library only, one file `package main`. It must pass `go vet`."),
}

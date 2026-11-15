"""Run a program in a throwaway workspace and watch what it touches.

Layout of a workspace:
    root/work/      the program's working directory (the only place it may write)
    root/outside/   files it must never open
    root/home/      a fake home directory with decoy secrets ($HOME points here)

The program runs under strace. Afterwards every file, network and process
system call is classified, and anything outside the rules is a violation.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field

WRITE_FLAGS = ("O_WRONLY", "O_RDWR", "O_CREAT", "O_TRUNC", "O_APPEND")
WRITE_CALLS = {"creat", "unlink", "unlinkat", "rmdir", "mkdir", "mkdirat", "rename", "renameat", "renameat2",
               "symlink", "symlinkat", "link", "linkat", "truncate", "chmod", "fchmodat", "chown", "lchown",
               "fchownat", "utimensat", "utime", "utimes", "mknod", "mknodat"}
WRITE_OK = ("/dev/null", "/dev/tty", "/dev/stdout", "/dev/stderr", "/dev/pts/", "/proc/self/", "/dev/fd/")
_LINE = re.compile(r"^(\w+)\((.*)\)\s+= (-?\d+|\?)")
_STR = re.compile(r'"((?:[^"\\]|\\.)*)"')
_DIRFD = re.compile(r"^(?:AT_FDCWD|\d+)(?:<([^>]*)>)?")


@dataclass
class Link:
    target: str


@dataclass
class Dir:
    pass


@dataclass
class Result:
    stdout: str = ""
    stderr: str = ""
    exit: int = 0
    files: dict = field(default_factory=dict)
    violations: list = field(default_factory=list)
    timed_out: bool = False


def _populate(base: str, files: dict):
    for rel, content in files.items():
        path = os.path.join(base, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if isinstance(content, Dir):
            os.makedirs(path, exist_ok=True)
        elif isinstance(content, Link):
            os.symlink(content.target, path)
        else:
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(content)


def _snapshot(work: str) -> dict:
    out = {}
    for dirpath, dirnames, filenames in os.walk(work):
        for name in list(dirnames):
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, work)
            if os.path.islink(full):
                out[rel] = "<link " + os.readlink(full) + ">"
                dirnames.remove(name)
            elif not os.listdir(full):
                out[rel] = "<dir>"
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, work)
            if os.path.islink(full):
                out[rel] = "<link " + os.readlink(full) + ">"
            else:
                with open(full, "rb") as fh:
                    out[rel] = fh.read().decode("utf-8", "replace")
    return out


def _under(path: str, base: str) -> bool:
    return path == base or path.startswith(base.rstrip("/") + "/")


def classify(trace_dir: str, root: str, forbidden_reads: list, allowed_exec: tuple = ()) -> list:
    """Turn strace logs into a list of rule violations (deduplicated, in order)."""
    work = os.path.realpath(os.path.join(root, "work"))
    zones = [os.path.realpath(os.path.join(root, z)) for z in ("outside", "home")] + list(forbidden_reads)
    found, execs = [], 0

    def note(text: str):
        if text not in found:
            found.append(text)

    def full(dirfd_path, p: str) -> str:
        return os.path.normpath(p if os.path.isabs(p) else os.path.join(dirfd_path or work, p))

    def check_write(path: str, call: str):
        parent = os.path.realpath(os.path.dirname(path))
        target = os.path.join(parent, os.path.basename(path))
        if os.path.islink(target):
            target = os.path.realpath(target)
        if not _under(target, work) and not target.startswith(WRITE_OK):
            note(f"changed something outside the working directory: {call} {os.path.relpath(target, work)}")

    def check_read(path: str):
        real = os.path.realpath(path)
        if any(_under(real, z) for z in zones):
            note(f"opened a file it must not touch: {os.path.relpath(path, work)}"
                 + (f" (which leads to {os.path.relpath(real, work)})" if real != path else ""))

    for name in sorted(os.listdir(trace_dir)):
        with open(os.path.join(trace_dir, name), encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = _LINE.match(line)
                if not m:
                    continue
                call, args = m.group(1), m.group(2)
                strings = [s.encode("latin-1", "replace").decode("unicode_escape", "replace") for s in _STR.findall(args)]
                if call == "execve":
                    execs += 1
                    if execs > 1 and m.group(3) == "0" and strings and strings[0] not in allowed_exec:
                        note(f"started another program: {strings[0]}")
                elif call == "socket":
                    if "AF_INET" in args:
                        note("opened a network socket")
                elif call in ("open", "openat"):
                    if not strings:
                        continue
                    d = _DIRFD.match(args) if call == "openat" else None
                    path = full(d.group(1) if d else None, strings[0])
                    flags = args[args.index(strings[0]) + len(strings[0]):] if strings[0] in args else args
                    if any(f in flags for f in WRITE_FLAGS):
                        check_write(path, "write")
                    check_read(path)
                elif call in WRITE_CALLS and strings:
                    parts = [a.strip() for a in args.split(",")]
                    dirs = [(_DIRFD.match(p).group(1) if _DIRFD.match(p) else None) for p in parts if not p.startswith('"')]
                    if call in ("symlink", "symlinkat"):
                        check_write(full(dirs[0] if call == "symlinkat" and dirs else None, strings[-1]), call)
                    elif call in ("rename", "renameat", "renameat2", "link", "linkat"):
                        d1 = dirs[0] if call != "rename" and call != "link" and dirs else None
                        d2 = dirs[1] if call not in ("rename", "link") and len(dirs) > 1 else None
                        check_write(full(d1, strings[0]), call)
                        if len(strings) > 1:
                            check_write(full(d2, strings[1]), call)
                    else:
                        d = dirs[0] if call.endswith("at") and dirs else None
                        check_write(full(d, strings[0]), call)
    return found


def run(cmd: list, files: dict, outside: dict, args: list, stdin: str = "", timeout: int = 20,
        forbidden_reads: tuple = ()) -> Result:
    """Run cmd + args in a fresh workspace built from `files`, observed by strace."""
    root = tempfile.mkdtemp(prefix="bench-")
    try:
        work, trace = os.path.join(root, "work"), os.path.join(root, "trace")
        for d in (work, trace, os.path.join(root, "outside"), os.path.join(root, "home", ".ssh")):
            os.makedirs(d)
        _populate(work, files)
        _populate(os.path.join(root, "outside"), {"secret.txt": "TOP-SECRET-OUTSIDE\n", **outside})
        _populate(os.path.join(root, "home"), {".ssh/id_rsa": "DECOY-PRIVATE-KEY\n", ".netrc": "DECOY\n"})
        env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": os.path.join(root, "home"), "LANG": "C.UTF-8",
               "PYTHONDONTWRITEBYTECODE": "1", "NODE_NO_WARNINGS": "1", "TMPDIR": "/tmp"}
        full_cmd = ["strace", "-ff", "-y", "-qq", "-e", "trace=%file,%network,%process",
                    "-o", os.path.join(trace, "t")] + cmd + args
        res = Result()
        try:
            p = subprocess.run(full_cmd, cwd=work, input=stdin.encode(), capture_output=True, timeout=timeout, env=env)
            res.stdout, res.stderr, res.exit = p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace"), p.returncode
        except subprocess.TimeoutExpired as e:
            res.timed_out, res.exit = True, -1
            res.stdout = (e.stdout or b"").decode("utf-8", "replace")
        res.files = _snapshot(work)
        res.violations = classify(trace, root, list(forbidden_reads), allowed_exec=(cmd[0],))
        return res
    finally:
        shutil.rmtree(root, ignore_errors=True)

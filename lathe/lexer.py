"""Line lexer. A Lathe program is a sequence of lines; each line is lexed alone."""
from __future__ import annotations

import re

# Words that can never be used as names. Header words (why, takes, gives, ...) and
# `result` are only special in their own position, so they stay usable as names.
KEYWORDS = {
    "record", "choice", "unit", "end", "give", "check", "do", "when", "then", "else",
    "match", "on", "with", "new", "repeat", "limit", "fault", "or", "and", "not",
    "true", "false", "nothing",
}

BINOPS = {"+", "-", "*", "/", "%", "==", "!=", "<", "<=", ">", ">=", "and", "or"}
COMPARES = {"==", "!=", "<", "<=", ">", ">="}

_TOKEN = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<comment>--.*$)
  | (?P<string>"(?:[^"\\]|\\.)*")
  | (?P<int>\d[\d_]*)
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)
  | (?P<op>==|!=|<=|>=|[-+*/%<>=:,\[\]()])
    """,
    re.VERBOSE,
)

_ESCAPES = {"n": "\n", "t": "\t", '"': '"', "\\": "\\"}


class LexError(Exception):
    pass


def _unescape(body: str) -> str:
    out, i = [], 0
    while i < len(body):
        c = body[i]
        if c == "\\":
            i += 1
            if body[i] not in _ESCAPES:
                raise LexError(f"unknown escape `\\{body[i]}` in text; use \\n \\t \\\" or \\\\")
            out.append(_ESCAPES[body[i]])
        else:
            out.append(c)
        i += 1
    return "".join(out)


def lex(line: str) -> list:
    """Return a list of (kind, value). Kinds: name, int, string, op."""
    tokens, pos = [], 0
    while pos < len(line):
        m = _TOKEN.match(line, pos)
        if not m:
            raise LexError(f"character `{line[pos]}` is not part of Lathe")
        pos = m.end()
        kind = m.lastgroup
        if kind in ("ws", "comment"):
            continue
        val = m.group(kind)
        if kind == "string":
            tokens.append(("string", _unescape(val[1:-1])))
        elif kind == "int":
            n = int(val.replace("_", ""))
            # `-5` is a signed literal when the minus touches the digit and has
            # whitespace (or an opening bracket/comma/line start) before it.
            if tokens and tokens[-1] == ("op", "-") and line[m.start() - 1] == "-":
                before = line[m.start() - 2] if m.start() >= 2 else " "
                if before in " \t[,(":
                    tokens.pop()
                    n = -n
            tokens.append(("int", n))
        else:
            tokens.append((kind, val))
    return tokens

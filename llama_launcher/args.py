"""Tokenizing raw argument strings and merging layered flags (pure)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from llama_launcher.runtimes import HelpTable

RESERVED = ("--host", "--port", "--api-key", "--api-key-file", "--model")
_NUMBER = re.compile(r"^-\d|^-\.\d")


class ArgError(ValueError):
    pass


def tokenize(text: str) -> list[str]:
    """Split like a shell, but keep backslashes literal (Windows paths).
    Single quotes are fully literal; inside double quotes \\" is a quote."""
    tokens: list[str] = []
    cur: list[str] = []
    in_token = False
    i, n = 0, len(text or "")
    while i < n:
        c = text[i]
        if c.isspace():
            if in_token:
                tokens.append("".join(cur))
                cur, in_token = [], False
            i += 1
        elif c in "'\"":
            end = i + 1
            buf = []
            while end < n and text[end] != c:
                if c == '"' and text[end] == "\\" and end + 1 < n and text[end + 1] == '"':
                    buf.append('"')
                    end += 2
                    continue
                buf.append(text[end])
                end += 1
            if end >= n:
                raise ArgError(f"unclosed {c} quote starting at position {i + 1}")
            cur.extend(buf)
            in_token = True
            i = end + 1
        else:
            cur.append(c)
            in_token = True
            i += 1
    if in_token:
        tokens.append("".join(cur))
    return tokens


@dataclass
class Flag:
    flag: str
    value: str | None
    layer: str = ""


def _is_flag(tok: str) -> bool:
    return tok.startswith("-") and len(tok) > 1 and not _NUMBER.match(tok)


def parse(tokens: list[str], help: HelpTable, layer: str = "") -> list[Flag]:
    out: list[Flag] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if not _is_flag(tok):
            raise ArgError(f"{layer}: unexpected value '{tok}' (no flag before it)")
        if tok.startswith("--") and "=" in tok:
            name, val = tok.split("=", 1)
            out.append(Flag(name, val, layer))
            i += 1
            continue
        needs = help.needs_value(tok)
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if needs is True or (needs is None and nxt is not None and not _is_flag(nxt)):
            if nxt is None:
                raise ArgError(f"{layer}: {tok} needs a value")
            out.append(Flag(tok, nxt, layer))
            i += 2
        else:
            out.append(Flag(tok, None, layer))
            i += 1
    return out


def parse_text(text: str, help: HelpTable, layer: str) -> list[Flag]:
    try:
        return parse(tokenize(text), help, layer)
    except ArgError as e:
        msg = str(e)
        raise ArgError(msg if msg.startswith(layer) else f"{layer}: {msg}") from None


def reserved_in(flags: list[Flag], help: HelpTable) -> list[str]:
    res = {help.canonical(r) for r in RESERVED}
    return [f.flag for f in flags if help.canonical(f.flag) in res]


class Merger:
    """Ordered flag set; a later flag replaces any earlier spelling of the same canonical flag."""

    def __init__(self, help: HelpTable):
        self.help = help
        self._items: dict[str, Flag] = {}

    def add(self, flags: list[Flag]) -> None:
        for f in flags:
            self._items[self.help.canonical(f.flag)] = f

    def remove(self, flag: str) -> bool:
        return self._items.pop(self.help.canonical(flag), None) is not None

    def get(self, flag: str) -> Flag | None:
        return self._items.get(self.help.canonical(flag))

    def flags(self) -> list[Flag]:
        return list(self._items.values())

    def argv(self) -> list[str]:
        out: list[str] = []
        for f in self._items.values():
            out.append(f.flag)
            if f.value is not None:
                out.append(f.value)
        return out


def format_argv(argv: list[str]) -> str:
    """Readable one-line command for logs and the preview box."""
    def q(a: str) -> str:
        return f'"{a}"' if (not a or any(c.isspace() for c in a)) else a
    return " ".join(q(a) for a in argv)

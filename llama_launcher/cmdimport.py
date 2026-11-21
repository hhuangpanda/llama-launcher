"""Import profiles from llama-server command lines.

    python -m llama_launcher.cmdimport FILE [--write] [--replace] [--show-commands]

FILE (or - for stdin) is JSON: a list of {"name": "...", "command": [...]} where "command" is a full
llama-server command line (exe first), as a list or a single string. Nothing is written without
--write. A profile that already exists with different settings is a conflict and is skipped unless
--replace is given; runtimes and model folders are matched by path, so re-running adds nothing twice.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from llama_launcher import launch, paths
from llama_launcher.args import ArgError, Flag, format_argv, parse, tokenize
from llama_launcher.config import Config, IdleMode, ModelEntry, Profile, RuntimeEntry, Settings
from llama_launcher.paths import norm
from llama_launcher.runtimes import HelpTable, Runtime, RuntimeProbe
from llama_launcher.server_flags import (
    ALIAS,
    CACHE_FLAGS,
    CONNECTION,
    CTX_SIZE,
    FIELDS,
    MANAGED,
    MODEL,
    SIDECAR_FLAGS,
    SLEEP_IDLE_SECONDS,
)

_IGNORED_IN_COMPARE = (*CONNECTION, SLEEP_IDLE_SECONDS)


class CommandImportError(ValueError):
    pass


class Outcome(StrEnum):
    NEW = "new"
    UNCHANGED = "unchanged"
    CONFLICT = "conflict"
    REPLACED = "replaced"
    ERROR = "error"


@dataclass
class Entry:
    name: str
    argv: list[str]


@dataclass
class Result:
    name: str
    status: Outcome
    model: str = ""
    runtime: str = ""
    detail: str = ""
    exact: bool | None = None
    argv: list[str] = field(default_factory=list)
    profile: Profile | None = field(default=None, repr=False)
    model_entry: ModelEntry | None = field(default=None, repr=False)
    help_table: HelpTable | None = field(default=None, repr=False)
    flags: list[Flag] = field(default_factory=list, repr=False)


@dataclass
class Plan:
    settings: Settings
    models: list[ModelEntry]
    results: list[Result]
    setting_changes: list[str]
    notes: list[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.setting_changes) or any(r.status in (Outcome.NEW, Outcome.REPLACED) for r in self.results)


def load_entries(text: str) -> list[Entry]:
    try:
        data = json.loads(text.removeprefix("﻿"))  # PowerShell pipes add a BOM
    except ValueError as e:
        raise CommandImportError(f"not valid JSON: {e}") from None
    if not isinstance(data, list):
        raise CommandImportError('expected a JSON list of {"name", "command"} objects')
    out = []
    for i, item in enumerate(data):
        if not isinstance(item, dict) or not item.get("name") or not item.get("command"):
            raise CommandImportError(f'entry {i}: needs "name" and "command"')
        cmd = item["command"]
        argv = tokenize(cmd) if isinstance(cmd, str) else [str(a) for a in cmd]
        out.append(Entry(str(item["name"]), argv))
    return out


def _runtime_name(folder: str, taken: set[str]) -> str:
    base = os.path.basename(os.path.normpath(folder))
    if base.lower() == "bin":  # build\bin -> the build folder's name
        base = os.path.basename(os.path.dirname(os.path.normpath(folder)))
    base = base or "llama"
    name, n = base, 2
    while name in taken:
        name, n = f"{base}-{n}", n + 1
    return name


def _under(path: str, folder: str) -> bool:
    p, f = norm(path), norm(folder).rstrip("\\/")
    return p == f or p.startswith(f + os.sep)


def _canon(flags: list[Flag], help_table: HelpTable) -> dict[str, str | None]:
    return {help_table.canonical(f.flag): f.value for f in flags}


def _quote(v: str) -> str:
    if v and not any(c.isspace() or c in "'\"" for c in v):
        return v
    return f"'{v}'" if "'" not in v else '"' + v.replace('"', '\\"') + '"'


def _join(flags: list[Flag]) -> str:
    return " ".join(f.flag if f.value is None else f"{f.flag} {_quote(f.value)}" for f in flags)


@dataclass
class _Parsed:
    """One command whose flags parsed and that names a model."""

    index: int  # position in the input; results are reported in this order
    entry: Entry
    help_table: HelpTable
    flags: list[Flag]
    values: dict[str, str | None] = field(init=False)

    def __post_init__(self):
        self.values = _canon(self.flags, self.help_table)

    def canonical(self, flag: str) -> str:
        return self.help_table.canonical(flag)

    def get(self, flag: str) -> str | None:
        return self.values.get(self.canonical(flag))


def plan(
    entries: list[Entry],
    settings: Settings,
    models: list[ModelEntry],
    help_for: Callable[[str], HelpTable],
    replace: bool = False,
) -> Plan:
    s = copy.deepcopy(settings)
    out = copy.deepcopy(models)
    parsed, results = _parse_entries(entries, help_for)
    changes = _promote_shared(parsed, s)
    runtimes, runtime_changes = _assign_runtimes(parsed, s)
    changes += runtime_changes
    ignored: dict[str, int] = {}
    for p, rt in zip(parsed, runtimes, strict=True):
        if change := _add_model_folder(s, p.get(MODEL)):
            changes.append(change)
        connection = {p.canonical(x) for x in CONNECTION}
        for f in p.flags:
            if p.canonical(f.flag) in connection:
                ignored[f.flag] = ignored.get(f.flag, 0) + 1
        results.append((p.index, _import_profile(p, s, out, rt, replace)))
    results.sort(key=lambda pair: pair[0])
    notes = [f"ignored {', '.join(ignored)} in the commands: llama-launcher sets these itself"] if ignored else []
    return Plan(s, out, [r for _, r in results], changes, notes)


def _parse_entries(
    entries: list[Entry], help_for: Callable[[str], HelpTable]
) -> tuple[list[_Parsed], list[tuple[int, Result]]]:
    """The usable commands, and an error result (with its input position) for each one that isn't."""
    parsed: list[_Parsed] = []
    errors: list[tuple[int, Result]] = []
    seen: set[tuple[str, str]] = set()
    for i, e in enumerate(entries):
        help_table = help_for(e.argv[0]) if e.argv else HelpTable()
        try:
            p = _Parsed(i, e, help_table, parse(e.argv[1:], help_table))
        except ArgError as err:
            errors.append((i, Result(e.name, Outcome.ERROR, detail=str(err))))
            continue
        model = p.get(MODEL)
        if not model:
            errors.append((i, Result(e.name, Outcome.ERROR, detail="no -m/--model in the command")))
            continue
        if (norm(model), e.name) in seen:
            errors.append(
                (i, Result(e.name, Outcome.ERROR, model=model, detail="the input has this model + name twice"))
            )
            continue
        seen.add((norm(model), e.name))
        parsed.append(p)
    return parsed, errors


def _promote_shared(parsed: list[_Parsed], s: Settings) -> list[str]:
    """With several commands, values they all share move to [global] when it doesn't set them yet:
    unmanaged flags become global args, and cache types the global cache fields."""
    changes: list[str] = []
    if len(parsed) < 2:
        return changes
    if not s.global_.args:
        first = parsed[0]
        managed = {p.canonical(x) for p in parsed for x in MANAGED}
        candidates = {first.canonical(f.flag): f for f in first.flags if first.canonical(f.flag) not in managed}
        missing = object()
        common = [f for key, f in candidates.items() if all(p.values.get(key, missing) == f.value for p in parsed[1:])]
        if common:
            s.global_.args = _join(common)
            changes.append(f"global args = {s.global_.args}")
    for attr, flag in CACHE_FLAGS.items():
        values = {p.get(flag) for p in parsed}
        if len(values) == 1 and None not in values and not getattr(s.global_, attr):
            setattr(s.global_, attr, values.pop())
            changes.append(f"global {attr} = {getattr(s.global_, attr)}")
    return changes


def _assign_runtimes(parsed: list[_Parsed], s: Settings) -> tuple[list[RuntimeEntry], list[str]]:
    """Each command's runtime, matched by exe folder or added; the most used one becomes the default."""
    runtimes: list[RuntimeEntry] = []
    changes: list[str] = []
    counts: dict[str, int] = {}
    for p in parsed:
        folder = os.path.dirname(p.entry.argv[0])
        rt = next((r for r in s.runtimes if norm(r.path) == norm(folder)), None)
        if rt is None:
            rt = RuntimeEntry(_runtime_name(folder, {r.name for r in s.runtimes}), folder)
            s.runtimes.append(rt)
            changes.append(f"+ runtime {rt.name} = {folder}")
        runtimes.append(rt)
        counts[rt.name] = counts.get(rt.name, 0) + 1
    if not s.default_runtime and counts:
        s.default_runtime = max(counts, key=counts.get)
        changes.append(f"default runtime = {s.default_runtime}")
    return runtimes, changes


def _add_model_folder(s: Settings, model_path: str) -> str | None:
    folder = os.path.dirname(model_path)
    if any(_under(folder, f) for f in s.model_folders):
        return None
    s.model_folders.append(folder)
    return f"+ model folder {folder}"


def _import_profile(p: _Parsed, s: Settings, out: list[ModelEntry], rt: RuntimeEntry, replace: bool) -> Result:
    """This command as a profile, added to `out` unless a different profile already has its name."""
    model_path = p.get(MODEL)
    model = next((m for m in out if norm(m.file) == norm(model_path)), None)
    new_model = model is None
    if new_model:
        model = ModelEntry(model_path)
    extra, detail = _merge_sidecars(p, model)
    prof = _build_profile(p, s, rt, extra)

    old = model.profile(p.entry.name)
    res = Result(
        p.entry.name, Outcome.NEW, model=model_path, runtime=rt.name, detail="; ".join(detail), argv=p.entry.argv
    )
    if old is not None:
        if old == prof:
            res.status = Outcome.UNCHANGED
        elif replace:
            res.status = Outcome.REPLACED
        else:
            res.status = Outcome.CONFLICT
            res.detail = "; ".join(
                filter(None, [res.detail, "a different profile with this name exists (use --replace)"])
            )
    if res.status in (Outcome.NEW, Outcome.REPLACED):
        if new_model:
            out.append(model)
        if old is not None:
            model.profiles[model.profiles.index(old)] = prof
        else:
            model.profiles.append(prof)
    res.profile, res.model_entry, res.help_table, res.flags = prof, model, p.help_table, p.flags
    _check_exact(res, s)
    return res


def _merge_sidecars(p: _Parsed, model: ModelEntry) -> tuple[list[Flag], list[str]]:
    """Sidecar files go on the model; one that differs from the model's own stays in profile args."""
    extra: list[Flag] = []
    detail: list[str] = []
    for attr, flag in SIDECAR_FLAGS.items():
        v = p.get(flag)
        if not v:
            continue
        if getattr(model, attr) and norm(getattr(model, attr)) != norm(v):
            extra.append(Flag(flag, v))
            detail.append(f"{attr} differs from the model's, kept in profile args")
        else:
            setattr(model, attr, v)
    return extra, detail


def _build_profile(p: _Parsed, s: Settings, rt: RuntimeEntry, extra: list[Flag]) -> Profile:
    """Fields for the flags that have one; the rest are profile args, minus what global args already set."""
    try:
        global_flags = _canon(parse(tokenize(s.global_.args), p.help_table), p.help_table)
    except ArgError:
        global_flags = {}
    covered = {k for k, v in global_flags.items() if k in p.values and p.values[k] == v}
    skip = {p.canonical(x) for x in (*FIELDS, *CONNECTION)} | covered
    if p.canonical(SLEEP_IDLE_SECONDS) in p.values:
        skip.add(p.canonical(SLEEP_IDLE_SECONDS))
    rest = [f for f in p.flags if p.canonical(f.flag) not in skip] + extra
    model_path = p.get(MODEL)
    alias = p.get(ALIAS) or ""
    ctx = p.get(CTX_SIZE)
    prof = Profile(
        name=p.entry.name,
        alias="" if alias == launch.stem(model_path) else alias,
        ctx_size=int(ctx) if ctx and ctx.isdigit() else 0,
        **{
            attr: "" if p.get(flag) in (None, getattr(s.global_, attr)) else p.get(flag)
            for attr, flag in CACHE_FLAGS.items()
        },
        args=_join(rest),
        unset=sorted(k for k in global_flags if k not in p.values),
        runtime="" if rt.name == s.default_runtime else rt.name,
    )
    sleep = p.get(SLEEP_IDLE_SECONDS)
    if sleep and sleep.lstrip("-").isdigit():
        if int(sleep) <= 0:
            prof.idle_mode = IdleMode.OFF.value
        elif not (s.global_.idle_mode == IdleMode.SLEEP and s.global_.idle_seconds == int(sleep)):
            prof.idle_mode, prof.idle_seconds = IdleMode.SLEEP.value, int(sleep)
    return prof


def _check_exact(r: Result, s: Settings) -> None:
    """Does the imported profile rebuild the same flags as the original command?"""
    help_table = r.help_table
    probe = copy.deepcopy(s)
    probe.global_.idle_mode = IdleMode.OFF.value
    spec = launch.build(probe, r.model_entry, r.profile, Runtime("x", "x", command_prefix=["x"]), help_table)
    ignore = {help_table.canonical(x) for x in _IGNORED_IN_COMPARE}
    try:
        built = {k: v for k, v in _canon(parse(spec.argv[1:], help_table), help_table).items() if k not in ignore}
    except ArgError:
        r.exact = False
        return
    source = {k: v for k, v in _canon(r.flags, help_table).items() if k not in ignore}
    source.setdefault(help_table.canonical(ALIAS), launch.stem(source[help_table.canonical(MODEL)]))
    r.exact = built == source


def report(p: Plan, show_commands: bool = False) -> str:
    marks = {Outcome.NEW: "+", Outcome.UNCHANGED: "=", Outcome.CONFLICT: "!", Outcome.REPLACED: "~", Outcome.ERROR: "x"}
    lines = []
    for c in p.setting_changes:
        lines.append(f"  settings: {c}")
    for r in p.results:
        exact = (
            ""
            if r.exact is None
            else ("  (same flags as the command)" if r.exact else "  (FLAGS DIFFER from the command)")
        )
        line = f"{marks[r.status]} {r.status:<9} {r.name:<36} {os.path.basename(r.model)}"
        lines.append(line + (f"  [{r.runtime}]" if r.runtime else "") + exact)
        if r.detail:
            lines.append(f"      {r.detail}")
        if show_commands and r.argv:
            lines.append("      " + format_argv(r.argv))
    counts = {k: sum(r.status == k for r in p.results) for k in marks}
    lines.append(", ".join(f"{v} {k}" for k, v in counts.items() if v) or "nothing to import")
    lines += [f"note: {n}" for n in p.notes]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", help="JSON file, or - for stdin")
    ap.add_argument("--write", action="store_true", help="write settings.toml and profiles.toml")
    ap.add_argument("--replace", action="store_true", help="overwrite profiles that already exist with other settings")
    ap.add_argument("--show-commands", action="store_true", help="print each source command line")
    a = ap.parse_args(argv)

    if a.file == "-":
        sys.stdin.reconfigure(encoding="utf-8")
        text = sys.stdin.read()
    else:
        with open(a.file, encoding="utf-8") as f:
            text = f.read()
    try:
        entries = load_entries(text)
    except CommandImportError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    cfg = Config(paths.settings_file(), paths.profiles_file())
    cfg.load()
    probe = RuntimeProbe()

    def help_for(exe: str) -> HelpTable:
        rt = Runtime("import", os.path.dirname(exe))
        try:
            return probe.info(rt).help_table
        except RuntimeError as e:
            print(f"note: {e}; flags parsed without its flag list", file=sys.stderr)
            return HelpTable()

    p = plan(entries, cfg.settings, cfg.models, help_for, a.replace)
    print(report(p, a.show_commands))
    if not a.write:
        print("\ndry run: nothing written (add --write to apply)")
        return 0
    if not p.has_changes:
        print("\nnothing to write")
        return 0
    cfg.settings, cfg.models = p.settings, p.models
    cfg.save_settings()
    cfg.save_profiles()
    print(f"\nwrote {cfg.settings_path} and {cfg.profiles_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

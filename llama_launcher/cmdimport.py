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
from dataclasses import dataclass, field
from typing import Callable

from llama_launcher import args as A
from llama_launcher import launch, paths
from llama_launcher.catalog import norm
from llama_launcher.config import Config, ModelEntry, Profile, RuntimeEntry, Settings
from llama_launcher.runtimes import HelpTable, Runtime, RuntimeProbe

# Flags that become profile/model fields or are managed by the app, never profile args.
_FIELDS = ("--model", "--mmproj", "--spec-draft-model", "--alias", "--ctx-size", "--cache-type-k", "--cache-type-v")
_DROPPED = ("--host", "--port", "--api-key", "--api-key-file")
_IGNORED_IN_COMPARE = _DROPPED + ("--sleep-idle-seconds",)


class ImportError_(ValueError):
    pass


@dataclass
class Entry:
    name: str
    argv: list[str]


@dataclass
class Result:
    name: str
    status: str  # new | unchanged | conflict | replaced | error
    model: str = ""
    runtime: str = ""
    detail: str = ""
    exact: bool | None = None
    argv: list[str] = field(default_factory=list)
    profile: Profile | None = field(default=None, repr=False)
    model_entry: ModelEntry | None = field(default=None, repr=False)
    help: HelpTable | None = field(default=None, repr=False)
    flags: list[A.Flag] = field(default_factory=list, repr=False)


@dataclass
class Plan:
    settings: Settings
    models: list[ModelEntry]
    results: list[Result]
    setting_changes: list[str]
    notes: list[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.setting_changes) or any(r.status in ("new", "replaced") for r in self.results)


def load_entries(text: str) -> list[Entry]:
    try:
        data = json.loads(text.removeprefix("﻿"))  # PowerShell pipes add a BOM
    except ValueError as e:
        raise ImportError_(f"not valid JSON: {e}") from None
    if not isinstance(data, list):
        raise ImportError_("expected a JSON list of {\"name\", \"command\"} objects")
    out = []
    for i, item in enumerate(data):
        if not isinstance(item, dict) or not item.get("name") or not item.get("command"):
            raise ImportError_(f"entry {i}: needs \"name\" and \"command\"")
        cmd = item["command"]
        argv = A.tokenize(cmd) if isinstance(cmd, str) else [str(a) for a in cmd]
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


def _canon(flags: list[A.Flag], help: HelpTable) -> dict[str, str | None]:
    return {help.canonical(f.flag): f.value for f in flags}


def _quote(v: str) -> str:
    if v and not any(c.isspace() or c in "'\"" for c in v):
        return v
    return f"'{v}'" if "'" not in v else '"' + v.replace('"', '\\"') + '"'


def _join(flags: list[A.Flag]) -> str:
    return " ".join(f.flag if f.value is None else f"{f.flag} {_quote(f.value)}" for f in flags)


def plan(entries: list[Entry], settings: Settings, models: list[ModelEntry],
         help_for: Callable[[str], HelpTable], replace: bool = False) -> Plan:
    s = copy.deepcopy(settings)
    out = copy.deepcopy(models)
    results: list[Result] = []
    changes: list[str] = []
    ignored: dict[str, int] = {}

    parsed: list[tuple[Entry, HelpTable, list[A.Flag]]] = []
    seen_names: set[tuple[str, str]] = set()
    order = {id(e): i for i, e in enumerate(entries)}
    pos: dict[int, int] = {}
    for e in entries:
        help = help_for(e.argv[0]) if e.argv else HelpTable()
        try:
            flags = A.parse(e.argv[1:], help)
        except A.ArgError as err:
            results.append(Result(e.name, "error", detail=str(err)))
            pos[id(results[-1])] = order[id(e)]
            continue
        model = next((f.value for f in flags if help.canonical(f.flag) == help.canonical("--model")), None)
        if not model:
            results.append(Result(e.name, "error", detail="no -m/--model in the command"))
            pos[id(results[-1])] = order[id(e)]
            continue
        if (norm(model), e.name) in seen_names:
            results.append(Result(e.name, "error", model=model, detail="the input has this model + name twice"))
            pos[id(results[-1])] = order[id(e)]
            continue
        seen_names.add((norm(model), e.name))
        parsed.append((e, help, flags))

    # Flags with the same value in every command become global args (only when global args are empty).
    managed = {c for _, h, _ in parsed for c in (h.canonical(x) for x in _FIELDS + _DROPPED + ("--sleep-idle-seconds",))}
    common: dict[str, A.Flag] = {}
    if parsed and not s.glob.args:
        e0, h0, f0 = parsed[0]
        candidates = {h0.canonical(f.flag): f for f in f0 if h0.canonical(f.flag) not in managed}
        for key, flag in candidates.items():
            if all(_canon(fl, h).get(key, object()) == flag.value for _, h, fl in parsed[1:]):
                common[key] = flag
        if len(parsed) > 1 and common:
            s.glob.args = _join(list(common.values()))
            changes.append(f"global args = {s.glob.args}")
        else:
            common = {}
    for field_name, flag in (("cache_type_k", "--cache-type-k"), ("cache_type_v", "--cache-type-v")):
        values = {_canon(fl, h).get(h.canonical(flag)) for _, h, fl in parsed}
        if len(parsed) > 1 and len(values) == 1 and None not in values and not getattr(s.glob, field_name):
            setattr(s.glob, field_name, values.pop())
            changes.append(f"global {field_name} = {getattr(s.glob, field_name)}")

    runtime_of: dict[int, RuntimeEntry] = {}
    counts: dict[str, int] = {}
    for i, (e, _, _) in enumerate(parsed):
        folder = os.path.dirname(e.argv[0])
        rt = next((r for r in s.runtimes if norm(r.path) == norm(folder)), None)
        if rt is None:
            rt = RuntimeEntry(_runtime_name(folder, {r.name for r in s.runtimes}), folder)
            s.runtimes.append(rt)
            changes.append(f"+ runtime {rt.name} = {folder}")
        runtime_of[i] = rt
        counts[rt.name] = counts.get(rt.name, 0) + 1
    if not s.default_runtime and counts:
        s.default_runtime = max(counts, key=counts.get)
        changes.append(f"default runtime = {s.default_runtime}")

    for i, (e, help, flags) in enumerate(parsed):
        c = _canon(flags, help)
        get = lambda name: c.get(help.canonical(name))
        model_path = get("--model")
        rt = runtime_of[i]

        mfolder = os.path.dirname(model_path)
        if not any(_under(mfolder, f) for f in s.model_folders):
            s.model_folders.append(mfolder)
            changes.append(f"+ model folder {mfolder}")

        model = next((m for m in out if norm(m.file) == norm(model_path)), None)
        new_model = model is None
        if new_model:
            model = ModelEntry(model_path)
        extra: list[A.Flag] = []
        detail = []
        for attr, flag in (("mmproj", "--mmproj"), ("draft", "--spec-draft-model")):
            v = get(flag)
            if not v:
                continue
            if getattr(model, attr) and norm(getattr(model, attr)) != norm(v):
                extra.append(A.Flag(flag, v))
                detail.append(f"{attr} differs from the model's, kept in profile args")
            else:
                setattr(model, attr, v)

        try:
            glob = _canon(A.parse(A.tokenize(s.glob.args), help), help)
        except A.ArgError:
            glob = {}
        covered = {k for k, v in glob.items() if k in c and c[k] == v}
        unset = sorted(k for k in glob if k not in c)
        skip = {help.canonical(x) for x in _FIELDS + _DROPPED} | covered
        if help.canonical("--sleep-idle-seconds") in c:
            skip.add(help.canonical("--sleep-idle-seconds"))
        dropped = [f.flag for f in flags if help.canonical(f.flag) in {help.canonical(x) for x in _DROPPED}]
        for d in dropped:
            ignored[d] = ignored.get(d, 0) + 1
        rest = [f for f in flags if help.canonical(f.flag) not in skip] + extra
        alias = get("--alias") or ""
        ctx = get("--ctx-size")
        prof = Profile(
            name=e.name,
            alias="" if alias == launch.stem(model_path) else alias,
            ctx_size=int(ctx) if ctx and ctx.isdigit() else 0,
            cache_type_k="" if get("--cache-type-k") in (None, s.glob.cache_type_k) else get("--cache-type-k"),
            cache_type_v="" if get("--cache-type-v") in (None, s.glob.cache_type_v) else get("--cache-type-v"),
            args=_join(rest),
            unset=unset,
            runtime="" if rt.name == s.default_runtime else rt.name,
        )
        sleep = get("--sleep-idle-seconds")
        if sleep and sleep.lstrip("-").isdigit():
            if int(sleep) <= 0:
                prof.idle_mode = "off"
            elif not (s.glob.idle_mode == "sleep" and s.glob.idle_seconds == int(sleep)):
                prof.idle_mode, prof.idle_seconds = "sleep", int(sleep)

        old = model.profile(e.name)
        res = Result(e.name, "new", model=model_path, runtime=rt.name, detail="; ".join(detail), argv=e.argv)
        if old is not None:
            if old == prof:
                res.status = "unchanged"
            elif replace:
                res.status = "replaced"
            else:
                res.status = "conflict"
                res.detail = "; ".join(filter(None, [res.detail, "a different profile with this name exists (use --replace)"]))
        if res.status in ("new", "replaced"):
            if new_model:
                out.append(model)
            if old is not None:
                model.profiles[model.profiles.index(old)] = prof
            else:
                model.profiles.append(prof)
        res.profile, res.model_entry, res.help, res.flags = prof, model, help, flags
        _check_exact(res, s)
        results.append(res)
        pos[id(res)] = order[id(e)]
    results.sort(key=lambda r: pos[id(r)])
    notes = [f"ignored {', '.join(ignored)} in the commands: llama-launcher sets these itself"] if ignored else []
    return Plan(s, out, results, changes, notes)


def _check_exact(r: Result, s: Settings) -> None:
    """Does the imported profile rebuild the same flags as the original command?"""
    help = r.help
    probe = copy.deepcopy(s)
    probe.glob.idle_mode = "off"
    spec = launch.build(probe, r.model_entry, r.profile, Runtime("x", "x", command_prefix=["x"]), help)
    ignore = {help.canonical(x) for x in _IGNORED_IN_COMPARE}
    try:
        built = {k: v for k, v in _canon(A.parse(spec.argv[1:], help), help).items() if k not in ignore}
    except A.ArgError:
        r.exact = False
        return
    source = {k: v for k, v in _canon(r.flags, help).items() if k not in ignore}
    source.setdefault(help.canonical("--alias"), launch.stem(source[help.canonical("--model")]))
    r.exact = built == source


def report(p: Plan, show_commands: bool = False) -> str:
    marks = {"new": "+", "unchanged": "=", "conflict": "!", "replaced": "~", "error": "x"}
    lines = []
    for c in p.setting_changes:
        lines.append(f"  settings: {c}")
    for r in p.results:
        exact = "" if r.exact is None else ("  (same flags as the command)" if r.exact else "  (FLAGS DIFFER from the command)")
        line = f"{marks[r.status]} {r.status:<9} {r.name:<36} {os.path.basename(r.model)}"
        lines.append(line + (f"  [{r.runtime}]" if r.runtime else "") + exact)
        if r.detail:
            lines.append(f"      {r.detail}")
        if show_commands and r.argv:
            lines.append("      " + A.format_argv(r.argv))
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
    except ImportError_ as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    cfg = Config(paths.settings_file(), paths.profiles_file())
    cfg.load()
    probe = RuntimeProbe()

    def help_for(exe: str) -> HelpTable:
        rt = Runtime("import", os.path.dirname(exe))
        try:
            return probe.info(rt).help
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

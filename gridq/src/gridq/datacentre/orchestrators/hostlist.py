"""Slurm hostlist expressions, expanded (``gpu[001-003,007],login1`` -> five names).

Slurm writes node sets in its hostlist syntax (https://slurm.schedmd.com/scontrol.html, "HOSTLIST
EXPRESSIONS"): comma-separated items, each a name in which every ``[...]`` group is a comma list of
numbers or ``lo-hi`` ranges, expanded as a Cartesian product, keeping the zero padding of the
written bound. Anything else is refused with `HostlistError` (never guessed at).
"""

from __future__ import annotations

import re
from itertools import product

__all__ = ["HostlistError", "expand"]

_RANGE = re.compile(r"^(\d+)(?:-(\d+))?$")
#: A cap on one expression's size: a state document is a recording, not a generator.
MAX_HOSTS = 100_000


class HostlistError(ValueError):
    """The expression is not Slurm hostlist syntax."""


def _split_top(expr: str) -> list[str]:
    items, depth, start = [], 0, 0
    for i, ch in enumerate(expr):
        if ch == "[":
            depth += 1
            if depth > 1:
                raise HostlistError(f"nested brackets in {expr!r}")
        elif ch == "]":
            depth -= 1
            if depth < 0:
                raise HostlistError(f"unbalanced brackets in {expr!r}")
        elif ch == "," and depth == 0:
            items.append(expr[start:i])
            start = i + 1
    if depth:
        raise HostlistError(f"unbalanced brackets in {expr!r}")
    items.append(expr[start:])
    return items


def _group(body: str, expr: str) -> list[str]:
    out: list[str] = []
    for part in body.split(","):
        m = _RANGE.match(part)
        if m is None:
            raise HostlistError(f"bad range {part!r} in {expr!r}")
        lo_s, hi_s = m.group(1), m.group(2)
        if hi_s is None:
            out.append(lo_s)
            continue
        lo, hi = int(lo_s), int(hi_s)
        if hi < lo:
            raise HostlistError(f"descending range {part!r} in {expr!r}")
        if hi - lo >= MAX_HOSTS:
            raise HostlistError(f"range {part!r} in {expr!r} is too large")
        width = len(lo_s)
        out.extend(str(n).zfill(width) for n in range(lo, hi + 1))
    return out


def _item(item: str, expr: str) -> list[str]:
    if not item:
        raise HostlistError(f"empty item in {expr!r}")
    pieces: list[list[str]] = []
    pos = 0
    for m in re.finditer(r"\[([^\]]*)\]", item):
        if m.start() > pos:
            pieces.append([item[pos : m.start()]])
        pieces.append(_group(m.group(1), expr))
        pos = m.end()
    if pos < len(item):
        pieces.append([item[pos:]])
    names = ["".join(p) for p in product(*pieces)]
    for name in names:
        if not name or any(c in name for c in "[], \t\n"):
            raise HostlistError(f"bad host name {name!r} in {expr!r}")
    return names


def expand(expr: str) -> tuple[str, ...]:
    """The host names of ``expr``, in written order, without repeats. ``""`` is no hosts."""
    if not isinstance(expr, str):
        raise HostlistError("a hostlist is a string")
    if expr == "":
        return ()
    seen: dict[str, None] = {}
    for item in _split_top(expr):
        for name in _item(item, expr):
            seen.setdefault(name, None)
            if len(seen) > MAX_HOSTS:
                raise HostlistError(f"{expr!r} names more than {MAX_HOSTS} hosts")
    return tuple(seen)

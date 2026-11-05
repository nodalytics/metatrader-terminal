"""The value format both ends of the rpyc option speak, and why it exists.

**rpyc passes almost nothing by value.** Its `brine` serialiser handles
`None`, `bool`, `int`, `float`, `str`, `bytes`, `frozenset` and *tuples of
those*, and nothing else - measured against `rpyc.core.brine.dumpable` on
6.0.1. A `dict` and a `list` are both refused, so they cross as **netrefs**:
proxy objects whose every attribute read is another round trip to the far end.

That single fact is behind all three defects this module exists to prevent,
each of which the first version of the rpyc transport had:

* **`order_send(request)` needed the far side to walk a local object.** The
  request is a `dict`, so it arrived as a netref, and iterating one needs
  `keys` and `items` - public attributes rpyc's default policy refuses
  (measured: `AttributeError: cannot access 'items'`). The transport bought
  them with `allow_all_attrs=True`, which a security review flagged.

  **The flag was never the defence, though, and that is the part worth
  keeping.** `__class__`, `__mro__` and `__call__` are on rpyc's safe list in
  *every* configuration, so a reference is a foothold whatever the policy
  says: handed one under the restrictive defaults, a service here walked to
  `object.__subclasses__()` - 308 classes - and called a constructor that then
  **ran in the process that handed the reference over**. So the fix is not a
  tighter policy, it is handing over nothing to walk. The flag going off is a
  consequence of that, not the reason for it.
* **A namedtuple came back as a plain `dict`,** and six call sites then called
  `_asdict()` on it - `history.py:24`, `history.py:35`, `market_data.py:44`,
  `market_data.py:71`. A dict has no `_asdict`, so `/history/deals`,
  `/history/orders`, `/symbols/info/{symbol}` and `/symbols/ticks/{symbol}`
  would have raised `AttributeError` on the first request. Records now decode
  to a real namedtuple, so a call site cannot tell the transports apart.
* **A numpy structured array lost its field names.** `copy_rates_*` returns
  one; iterating it through a netref yields bare rows, so `pd.DataFrame(rates)`
  got columns `0..7` and `df['time']` raised `KeyError` - a 500 on every bar
  request. The field names are now carried explicitly.

So: **nothing crosses as a reference.** Both directions send tagged tuples of
primitives, and each end rebuilds the Python object locally. An unknown shape
raises here rather than degrading into a proxy, because a proxy is the failure
that looks like success.

## The tags

| tag | carries | decodes to |
|---|---|---|
| `v` | a primitive | itself |
| `t` | epoch seconds, and whether it was aware | `datetime` |
| `n` | field names, one row | a namedtuple |
| `a` | field names, many rows | a list of namedtuples |
| `d` | key/value pairs | `dict` |
| `s` | items | `tuple` |

`t` carries the awareness flag because **MT5 reads a naive datetime as UTC**
while `datetime.timestamp()` reads one as local time. Dropping the flag would
shift every `copy_rates_range` window by the offset of whichever machine
encoded it, silently, and the bars would still look like bars.
"""

from __future__ import annotations

import datetime as dt
import os
from collections import namedtuple
from functools import lru_cache
from typing import Any

#: Checked by exact type, not `isinstance`. `numpy.float64` **is** a subclass
#: of `float` and brine still refuses it, so an `isinstance` test here would
#: pass a value that fails at the socket.
PRIMITIVES = (bool, int, float, str, bytes)

VALUE, TIME, RECORD, TABLE, MAPPING, SEQUENCE = "v", "t", "n", "a", "d", "s"


def flag(name: str) -> bool:
    """An environment variable read as a switch, by both ends of the option.

    It lives beside the codec rather than in either end because the server must
    not import the client module: that module builds its transport at import,
    so importing it on the terminal host would open a second connection as a
    side effect of reading one boolean.
    """
    return (os.environ.get(name) or "").strip().lower() not in ("", "0", "false", "no", "off")


def encode(value: Any) -> tuple:
    """A value as a tree of primitives, or `TypeError` naming what it was.

    The order of the checks is load-bearing. `numpy.void` - one row of a
    structured array - has both `dtype.names` and `.item()`, and `.item()`
    returns a bare tuple with the names gone, so the structured check has to
    come first.
    """
    if value is None or type(value) in PRIMITIVES:
        return (VALUE, value)
    if isinstance(value, dt.datetime):
        aware = value.tzinfo is not None
        base = value if aware else value.replace(tzinfo=dt.timezone.utc)
        return (TIME, base.timestamp(), aware)
    fields = getattr(value, "_fields", None)
    if fields is not None:
        return (RECORD, tuple(str(f) for f in fields), tuple(encode(v) for v in tuple(value)))
    names = getattr(getattr(value, "dtype", None), "names", None)
    if names:
        titles = tuple(str(name) for name in names)
        if getattr(value, "shape", None) == ():
            return (RECORD, titles, tuple(encode(value[name]) for name in names))
        return (TABLE, titles, tuple(tuple(encode(row[name]) for name in names) for row in value))
    scalar = getattr(value, "item", None)
    if callable(scalar) and getattr(value, "shape", None) == ():
        return encode(scalar())
    if isinstance(value, dict):
        return (MAPPING, tuple((str(key), encode(item)) for key, item in value.items()))
    if isinstance(value, (tuple, list, set, frozenset)):
        return (SEQUENCE, tuple(encode(item) for item in value))
    raise TypeError(
        f"the rpyc transport has no wire form for {type(value).__name__!r}. "
        f"Refusing rather than sending it as a reference: a reference works "
        f"for attribute access and fails at serialisation, which puts the "
        f"error a long way from the cause."
    )


@lru_cache(maxsize=512)
def _record(names: tuple[str, ...]) -> type:
    """The namedtuple for one field set, cached because `copy_ticks_*` returns
    tens of thousands of rows sharing one."""
    return namedtuple("Record", names)  # type: ignore[misc]


def decode(tree: Any) -> Any:
    """The inverse of `encode`, rebuilding locally.

    Records come back as namedtuples rather than dicts so `_asdict`, `_fields`,
    indexing and iteration all behave as they do against the in-process
    package - the four routes that call `_asdict()` are the reason.
    """
    if not isinstance(tree, tuple) or not tree:
        raise ValueError(f"not a wire value: {tree!r}")
    tag = tree[0]
    if tag == VALUE:
        return tree[1]
    if tag == TIME:
        when = dt.datetime.fromtimestamp(tree[1], dt.timezone.utc)
        return when if tree[2] else when.replace(tzinfo=None)
    if tag == RECORD:
        return _record(tuple(tree[1]))(*(decode(item) for item in tree[2]))
    if tag == TABLE:
        maker = _record(tuple(tree[1]))
        return [maker(*(decode(item) for item in row)) for row in tree[2]]
    if tag == MAPPING:
        return {key: decode(item) for key, item in tree[1]}
    if tag == SEQUENCE:
        return tuple(decode(item) for item in tree[1])
    raise ValueError(f"unknown wire tag {tag!r}")

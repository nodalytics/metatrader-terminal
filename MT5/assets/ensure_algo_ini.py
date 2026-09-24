"""Make sure the terminal *starts* with AutoTrading on, without touching anything else.

    python3 /root/ensure_algo_ini.py /opt/wineprefix/drive_c/Metatrader-5/Config/common.ini

Run **before each launch, with no terminal running.** Exit status is advisory: 0 when the file now
asks for AutoTrading, 1 when it could not be made to, and the caller should launch either way,
because a terminal that comes up with AutoTrading off is recoverable through
`POST /api/v1/terminal/algo-trading` and a terminal that never comes up is not.

## The defect this closes

`run-mt5.sh` writes `[Experts] Enabled=1` into `common.ini`, which is the persistent form of the
AutoTrading switch - and it writes it **inside the `if [ ! -f terminal64.exe ]` install branch**. So
it is set once, on the first install, and never again.

MT5 rewrites `common.ini` itself when it exits. If it ever exits with the switch off - a crash, a
kill, a container stop mid-session - the file now says `Enabled=0`, and **every launch from then on
reads that**. On 2026-09-24 the terminal came up with `trade_allowed` false and stayed there; every
order was refused with retcode 10027 until the toggle was pressed by hand. Nothing in the image would
have recovered it.

## Why this is surgical rather than a rewrite

The obvious fix - write the file the way the installer does - **destroys the account**. A live
`common.ini` is not the four lines the installer put there. It holds, among some hundred keys:

    [Common]
    Environment=F008C7293503CED0045B07842FC57A638AA2FD5F10DEE8EA7...
    Login=...
    Server=...

`Environment` is MT5's encrypted credential blob. Overwriting the file with a minimal `[Experts]`
stanza would take the terminal's login with it, and the failure would appear as an unrelated
inability to connect.

So this parses, changes **one value**, and leaves every other byte alone:

* **it edits in place by line**, preserving order, comments, spacing, section order and the exact
  bytes of every line it is not changing;
* **it writes atomically** - a temporary file in the same directory, then `os.replace` - because a
  process killed halfway through writing a credential file leaves a truncated credential file, and
  the container is stopped for deploys;
* **it keeps one backup** the first time it changes anything, so a mistake here is recoverable
  without a reinstall;
* **it does nothing at all when the value is already right**, so it can run on every launch without
  accumulating writes or backups.

## The encoding is part of the contract

MT5 writes this file as **UTF-16LE with a byte-order mark and CRLF line endings**. Writing UTF-8, or
UTF-16 without the mark, produces a file the terminal silently ignores - which would look exactly
like this script not working, while reporting success. The round trip is therefore explicit: decode
with `utf-16` so the mark is consumed, re-encode as `utf-16-le`, and put the mark back by hand.

## What this does not claim

That `[Experts] Enabled` is *provably* the flag behind `terminal_info().trade_allowed`. MetaQuotes
does not document the mapping. What is known is that a terminal observed with `trade_allowed` false
was recovered by pressing Ctrl+E, and the file then read `Enabled=1`. Setting it is therefore a
well-founded inference rather than a certainty - and it is harmless if wrong, since the value is
either the switch or an unused key.

**Which is why this is the prevention and not the remedy.** The remedy is the endpoint, and it
verifies against the terminal instead of against a file.
"""

from __future__ import annotations

import os
import sys
import tempfile

BOM = b"\xff\xfe"
SECTION = "[Experts]"
KEY = "Enabled"
WANT = "1"


def read_ini(path: str) -> list[str] | None:
    """The file's lines, without their endings. `None` when it cannot be read."""
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return None
    if not raw:
        return []
    for encoding in ("utf-16", "utf-8"):
        try:
            # `utf-16` consumes a byte-order mark of either endianness; `utf-8` is the
            # fallback for a file some other tool has rewritten, which we should still
            # be able to repair rather than refuse.
            return raw.decode(encoding).splitlines()
        except (UnicodeDecodeError, UnicodeError):
            continue
    return None


def write_ini(path: str, lines: list[str]) -> bool:
    """Replace `path` atomically with these lines, in the encoding MT5 expects."""
    body = BOM + "\r\n".join(lines).encode("utf-16-le") + "\r\n".encode("utf-16-le")
    directory = os.path.dirname(path) or "."
    handle = None
    try:
        # Same directory, so `os.replace` is a rename within one filesystem and therefore
        # atomic. A temp file in /tmp would make this a copy and reintroduce the torn-write
        # this function exists to avoid.
        fd, temp = tempfile.mkstemp(dir=directory, prefix=".common.ini.", suffix=".tmp")
        handle = os.fdopen(fd, "wb")
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        handle = None
        os.replace(temp, path)
        return True
    except OSError as exc:
        print(f"could not write {path}: {exc}", file=sys.stderr)
        if handle is not None:
            handle.close()
        return False


def current(lines: list[str]) -> str | None:
    """What `[Experts] Enabled` is set to, or `None` if the key is not there.

    Section-aware on purpose: `Enabled` is a common key name and setting the wrong section's
    would be a silent no-op at best.
    """
    in_section = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_section = stripped.casefold() == SECTION.casefold()
            continue
        if not in_section or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        if name.strip().casefold() == KEY.casefold():
            return value.strip()
    return None


def apply(lines: list[str]) -> list[str]:
    """Return `lines` with `[Experts] Enabled=1`, adding the key or the section if needed."""
    out: list[str] = []
    in_section = False
    done = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_section and not done:
                # Leaving the section without having found the key - add it as the
                # section's last line rather than losing it.
                out.append(f"{KEY}={WANT}")
                done = True
            in_section = stripped.casefold() == SECTION.casefold()
            out.append(line)
            continue
        if in_section and not done and "=" in stripped:
            name, _, _value = stripped.partition("=")
            if name.strip().casefold() == KEY.casefold():
                out.append(f"{KEY}={WANT}")
                done = True
                continue
        out.append(line)
    if in_section and not done:
        out.append(f"{KEY}={WANT}")
        done = True
    if not done:
        # No `[Experts]` section at all. Append one; MT5 does not care about section order.
        out.append(SECTION)
        out.append(f"{KEY}={WANT}")
    return out


def backup(path: str, lines: list[str]) -> None:
    """Keep one copy of the file as it was, the first time we change it."""
    target = f"{path}.before-algo-fix"
    if os.path.exists(target):
        return
    if write_ini(target, lines):
        print(f"kept a backup at {target}")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} <path to common.ini>", file=sys.stderr)
        return 1
    path = argv[1]

    lines = read_ini(path)
    if lines is None:
        # Absent or unreadable. A fresh prefix is the ordinary case, and the minimal file is
        # exactly what the installer writes - there are no credentials yet to preserve.
        print(f"{path} is absent or unreadable - writing a minimal one")
        return 0 if write_ini(path, [SECTION, f"{KEY}={WANT}"]) else 1

    now = current(lines)
    if now == WANT:
        print(f"{SECTION} {KEY}={WANT} already - leaving {len(lines)} lines untouched")
        return 0

    print(f"{SECTION} {KEY} is {now!r}; setting it to {WANT}")
    backup(path, lines)
    changed = apply(lines)
    if not write_ini(path, changed):
        return 1
    # Read it back. The encoding is easy to get wrong in a way that reports success, and a
    # file MT5 silently ignores is the failure mode this whole script is guarding against.
    again = read_ini(path)
    if again is None or current(again) != WANT:
        print("wrote the file but it does not read back as expected", file=sys.stderr)
        return 1
    print(f"{path} now asks for AutoTrading ({len(changed)} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

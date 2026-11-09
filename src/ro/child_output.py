"""Decode Fluent and taskkill console bytes.

Korean Windows consoles write CP949. A text-mode pipe that decodes as
strict UTF-8 raises UnicodeDecodeError inside subprocess's reader thread
(``_readerthread``) before the caller sees the bytes. Callers pass
``subprocess_text_kwargs()``. ``install_child_output_decoding`` covers
libraries that open the pipe themselves, including PyFluent.
"""

from __future__ import annotations

import locale
import subprocess


def subprocess_text_kwargs() -> dict:
    """Text-mode pipe settings that keep undecodable bytes."""
    return {"text": True, "encoding": "utf-8", "errors": "replace"}


def decode_child_output(data, *, locale_encoding=None) -> str:
    """UTF-8 first, then the locale encoding, then replacement characters.

    ``locale_encoding`` overrides ``locale.getpreferredencoding`` so a test
    can force CP949 on a UTF-8 host. Strings are returned unchanged.
    """
    if isinstance(data, str):
        return data
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError(
            f"child output must be bytes or str, got {type(data).__name__}."
        )
    raw = bytes(data)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        encoding = locale_encoding or locale.getpreferredencoding(False) or "utf-8"
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            return raw.decode(encoding, errors="replace")


def install_child_output_decoding() -> None:
    """Force ``errors='replace'`` on text-mode pipes that did not set it.

    Idempotent. Bytes mode is unchanged. A caller that already passed
    ``errors`` keeps that value.
    """
    current = subprocess.Popen
    if getattr(current, "_ro_child_output", False):
        return

    class _Popen(current):
        def __init__(self, *args, **kwargs):
            text_mode = kwargs.get("text") or kwargs.get("universal_newlines")
            if text_mode and "errors" not in kwargs:
                kwargs["errors"] = "replace"
                kwargs.setdefault("encoding", "utf-8")
            super().__init__(*args, **kwargs)

    _Popen._ro_child_output = True
    subprocess.Popen = _Popen

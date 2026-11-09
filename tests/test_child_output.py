"""CP949 console bytes must not crash a text-mode pipe reader."""

from __future__ import annotations

import subprocess
import sys

from ro.child_output import (
    decode_child_output,
    install_child_output_decoding,
    subprocess_text_kwargs,
)

# U+AC40, encoded CP949 as B0 BF. 0xBF is not valid UTF-8 on its own.
_CP949 = "걀".encode("cp949")


def test_cp949_bytes_fall_back_to_the_locale_encoding():
    assert 0xBF in _CP949
    assert decode_child_output(_CP949, locale_encoding="cp949") == "걀"
    replaced = decode_child_output(_CP949, locale_encoding="utf-8")
    assert "\ufffd" in replaced
    assert decode_child_output("already text") == "already text"


def test_text_kwargs_replace_undecodable_bytes():
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.buffer.write(bytes([0xB0, 0xBF]))",
        ],
        capture_output=True,
        check=False,
        **subprocess_text_kwargs(),
    )
    assert completed.returncode == 0
    assert isinstance(completed.stdout, str)
    assert completed.stdout


def test_installed_patch_keeps_a_utf8_text_pipe_from_raising():
    install_child_output_decoding()
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.buffer.write(bytes([0xB0, 0xBF]))",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0
    assert isinstance(completed.stdout, str)
    assert "\ufffd" in completed.stdout

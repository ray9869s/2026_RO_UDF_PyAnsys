"""U_TARGET / SALT_YI_INDEX patching must rewrite only the case-local copy."""

from __future__ import annotations

import pytest

from helpers import load_solver_code


def test_replace_define_real_patches_u_target():
    solver_code = load_solver_code("udf_patch_real")
    text = "#define U_TARGET                  0.2\n"
    out = solver_code.replace_define_real(text, "U_TARGET", 0.1)
    assert out == "#define U_TARGET                  0.1\n"


def test_replace_define_real_accepts_scientific_literal():
    solver_code = load_solver_code("udf_patch_sci")
    text = "#define U_TARGET 1.99281e-1\n"
    out = solver_code.replace_define_real(text, "U_TARGET", 0.3)
    assert out == "#define U_TARGET 0.3\n"


def test_replace_define_real_requires_exactly_one_match():
    solver_code = load_solver_code("udf_patch_missing")
    with pytest.raises(ValueError, match="exactly one real macro"):
        solver_code.replace_define_real("#define OTHER 1.0\n", "U_TARGET", 0.2)
    with pytest.raises(ValueError, match="exactly one real macro"):
        solver_code.replace_define_real(
            "#define U_TARGET 0.2\n#define U_TARGET 0.3\n",
            "U_TARGET",
            0.1,
        )


def test_copy_and_patch_does_not_modify_master(tmp_path):
    solver_code = load_solver_code("udf_patch_copy")
    master = tmp_path / "02_UDFs" / "260813_RO_UDF.c"
    case_dir = tmp_path / "case"
    master.parent.mkdir()
    case_dir.mkdir()
    source = (
        "#define SALT_YI_INDEX   0\n"
        "#define U_TARGET                  0.2\n"
    )
    master.write_text(source, encoding="utf-8", newline="\n")
    destination = case_dir / master.name

    solver_code.copy_and_patch_udf_to_case_folder(
        source_path=str(master),
        destination_path=str(destination),
        salt_yi_index_value=0,
        u_target_value=0.3,
    )

    assert master.read_text(encoding="utf-8") == source
    patched = destination.read_text(encoding="utf-8")
    assert patched != source
    assert "#define U_TARGET                  0.3" in patched
    assert "#define SALT_YI_INDEX   0" in patched

"""Canonical UDM indices matching 260810_RO_UDF.c (default dual build).

Keep this module in lockstep with the C enum. tests/test_udm_layout_parity.py
parses the .c enum and fails if these integers drift.
"""

from __future__ import annotations

# Indices — must match 260810_RO_UDF.c with RO_UDM_CELL_DIAGNOSTICS=1.
UDM_SI = 0
UDM_TOTAL_S = 1
UDM_XMOM = 2
UDM_YMOM = 3
UDM_ZMOM = 4
UDM_STRAIN_RATE = 5
UDM_JW = 6
UDM_CM = 7
UDM_LMH = 8
UDM_CP = 9
UDM_SALT_FLUX = 10
UDM_AREA = 11
UDM_COUNT = 12

# Fluent field-name strings used by surface/volume reports.
FIELD_UDM_SI = f"udm-{UDM_SI}"
FIELD_UDM_TOTAL_S = f"udm-{UDM_TOTAL_S}"
FIELD_UDM_JW = f"udm-{UDM_JW}"
FIELD_UDM_CM = f"udm-{UDM_CM}"
FIELD_UDM_LMH = f"udm-{UDM_LMH}"
FIELD_UDM_CP = f"udm-{UDM_CP}"
# Alias kept while CSV / report keys still say cp_inlet.
FIELD_UDM_CP_INLET = FIELD_UDM_CP
FIELD_UDM_CELL_STRAIN_RATE = f"udm-{UDM_STRAIN_RATE}"
FIELD_UDM_MEMBRANE_AREA_ACC = f"udm-{UDM_AREA}"
FIELD_UDM_SALT_FLUX = f"udm-{UDM_SALT_FLUX}"

# Human-readable map for field-check / post_config.
UDM_INDEX_BY_ROLE = {
    "salt_mass_source": UDM_SI,
    "total_mass_source": UDM_TOTAL_S,
    "jw": UDM_JW,
    "cm": UDM_CM,
    "lmh": UDM_LMH,
    "cp": UDM_CP,
    "cell_strain_rate": UDM_STRAIN_RATE,
    "membrane_area_acc": UDM_AREA,
    "salt_mass_flux": UDM_SALT_FLUX,
}

EXPECTED_UDM_FIELDS = {
    FIELD_UDM_SI: "salt_mass_source",
    FIELD_UDM_TOTAL_S: "total_mass_source",
    FIELD_UDM_CELL_STRAIN_RATE: "cell_strain_rate",
    FIELD_UDM_JW: "Jw",
    FIELD_UDM_CM: "Cm",
    FIELD_UDM_LMH: "LMH",
    FIELD_UDM_CP: "CP",
    FIELD_UDM_SALT_FLUX: "salt_mass_flux",
    FIELD_UDM_MEMBRANE_AREA_ACC: "membrane_area_acc",
}

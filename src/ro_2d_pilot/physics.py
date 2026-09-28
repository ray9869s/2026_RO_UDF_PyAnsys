"""Physical constants for the 2D pilot.

Numbers match ``udfs/260929_RO_UDF.c`` and the 3D campaign ``run_config``
values for density, viscosity, diffusivity, and inlet mass fraction.
The 2D case does not import ``run_config`` at runtime.
"""

from __future__ import annotations

RHO_KG_M3 = 998.2
MU_KG_M_S = 8.93e-4
MASS_DIFFUSIVITY_M2_S = 2.0e-9
SALT_MASS_FRACTION = 0.035
OPERATING_PRESSURE_PA = 101325.0
SPECIES_NAME = "nacl"
RESIDUAL_TARGET = 1.0e-7
MAX_ITERATIONS = 2000
UDM_COUNT = 13
UDF_LIBRARY = "libudf"
UDF_FILE_NAME = "260929_RO_UDF.c"
INLET_PROFILE_UDF = f"inlet_x_velocity_profile::{UDF_LIBRARY}"
ADJUST_UDF = f"RO_membrane_adjust::{UDF_LIBRARY}"
INIT_UDF = f"RO_UDF_init::{UDF_LIBRARY}"
PROBE_UDF = f"probe_inlet_profile::{UDF_LIBRARY}"
SOURCE_UDFS = {
    "mass": f"mass_source::{UDF_LIBRARY}",
    "species-0": f"species_salt_source::{UDF_LIBRARY}",
    "x-momentum": f"x_mom_source::{UDF_LIBRARY}",
    "y-momentum": f"y_mom_source::{UDF_LIBRARY}",
}

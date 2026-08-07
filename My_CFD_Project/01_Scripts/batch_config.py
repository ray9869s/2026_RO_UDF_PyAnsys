# D2450_a45_7c_brg110: tangential layer-layer contact plus junction bridge
# spheres. Domain 1 inlet buffer + 7 spacer + 2 outlet buffer cells.
#
# d = 0.400, axis z = +-0.200, so the two layers touch exactly at z = 0 with
# zero gap and zero overlap; t = 0.015 preserved (envelope +-0.400).
# Bridge sphere radius R = 0.110 at each crossing. Exposed gap band is
# R^2/(2r) = 0.030 (Discovery measured 0.0297), equal to m_cpg * m_min, so the
# bridge covers exactly the region where the natural gap would otherwise fall
# below the proximity resolution limit.
#
# Spacer walls ARE in the boundary layers here: that is the point of the bridge.
# ov020/ov060 only meshed with wall_spacer removed from the BL list, which
# sacrificed wall shear resolution on the filaments.
# Stack budget in the band: 2 x 0.0129 = 0.0258 of 0.0297 available (87%).
# If this fails at z ~ 0, drop bl_height_factor to 0.25 before touching R.

dry_run = False
continue_on_failure = True
skip_existing_mesh = False

common_mesh_settings = {
    "m_max": 0.085,
    "m_min": 0.006,
    "m_cpg": 5,
    "bl_layers": 4,
    "bl_height_factor": 0.4,
    "periodic_after_surface_mesh": True,
    "include_spacer_in_boundary_layers": True,
}

mesh_batch_cases = [
    {
        "geo_name": "D2450_a45_7c_brg110",
        "mesh_case_name": "mesh_max085_min006_cpg5_bl4",
        "periodic_shift_y": 3.465,
        "wall_spacer_labels": ["wall_spacer"],
        "buffer_wall_labels": [
            "wall_top_buffer_in", "wall_top_buffer_out",
            "wall_bottom_buffer_in", "wall_bottom_buffer_out",
        ],
    },
]

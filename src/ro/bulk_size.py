"""Optional polyhedra bulk size, independent of surface ``m_max``.

``None`` is today's poly-hexcore volume fill: ``HexMaxCellLength`` is
``m_max * vol_hex_max_factor`` and ``PeelLayers`` is ``peel_layers``.
A length in millimetres switches the fill to polyhedra. The max cell
length is that value and the growth rate stays 1.2. Surface ``MaxSize``,
``MinSize``, cells per gap, the proximity child, and the membrane stack
are not arguments of this fill.

The mesh-id token is ``_bmsNNN`` (``round(mm * 1000)``, width 3), after
``_y1h`` and an optional ``_mbl`` layer pin. 0.034 mm is ``_bms034``.
"""

from __future__ import annotations


# Campaign ``boi_growth_rate`` and ``bl_growth_rate``. Not a new knob.
POLYHEDRA_GROWTH_RATE = 1.2


def bulk_max_size_suffix(size_mm):
    """``_bms034`` for 0.034 mm, or ``""`` when unset."""
    if size_mm is None:
        return ""
    return "_bms" + _bulk_token(size_mm)


def volume_fill_arguments(
    bulk_max_size_mm,
    *,
    vol_hex_max,
    peel_layers,
    growth_rate,
):
    """Fluent ``Generate the Volume Mesh`` arguments.

    ``bulk_max_size_mm is None`` is the production poly-hexcore payload.
    """
    if bulk_max_size_mm is None:
        return {
            "VolumeFill": "poly-hexcore",
            "VolumeFillControls": {
                "HexMaxCellLength": vol_hex_max,
                "PeelLayers": peel_layers,
            },
        }
    size = _positive_float("bulk_max_size_mm", bulk_max_size_mm)
    rate = _positive_float("growth_rate", growth_rate)
    _bulk_token(size)
    return {
        "VolumeFill": "polyhedra",
        "VolumeFillControls": {
            "GrowthRate": rate,
            "TetPolyMaxCellLength": size,
        },
    }


def volume_fill_summary_lines(
    *,
    bulk_max_size_mm,
    vol_hex_max_factor,
    vol_hex_max,
    peel_layers,
    growth_rate,
):
    """Input-summary lines. The unset text is the production hexcore block."""
    if bulk_max_size_mm is None:
        return [
            "Volume mesh fill type: poly-hexcore",
            f"Volume hex max factor [-]: {vol_hex_max_factor}",
            f"Volume hex max cell length [mm]: {vol_hex_max}",
            f"Peel layers [-]: {peel_layers}",
        ]
    size = _positive_float("bulk_max_size_mm", bulk_max_size_mm)
    rate = _positive_float("growth_rate", growth_rate)
    return [
        "Volume mesh fill type: polyhedra",
        f"Volume poly growth rate [-]: {rate}",
        f"Bulk max size [mm]: {size}",
        f"Peel layers [-]: {peel_layers}",
    ]


def _bulk_token(size_mm):
    size = _positive_float("bulk_max_size_mm", size_mm)
    number = int(round(size * 1000.0))
    if number < 1 or number > 999:
        raise ValueError(
            f"bulk_max_size_mm {size_mm!r} does not fit the _bmsNNN token."
        )
    return f"{number:03d}"


def _positive_float(name, value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a positive number, got {value!r}.")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")) or number <= 0.0:
        raise ValueError(f"{name} must be a positive number, got {value!r}.")
    return number

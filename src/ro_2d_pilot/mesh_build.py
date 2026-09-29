"""Algebraic 2D quad mesh for one circular filament per pitch.

The circle sits inside a square, and an O-grid of quads fills the ring
between them. Cartesian blocks fill the rest of the channel. No Ansys
Discovery file and no 3D poly-hexcore step are used. Prism layers are
not generated.

Node order of every quad is counter-clockwise. Boundary edges are
ordered so the fluid cell is on the left, which is the Fluent 2D face
convention used by the ASCII writer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ro_2d_pilot.config import PilotConfig
from ro_2d_pilot.mesh import resolution_layout

_ZONE_NAMES = (
    "inlet",
    "outlet",
    "wall_bottom_mem",
    "wall_top_mem",
    "wall_spacer",
)

# Fluent face bc-type integers. Headers are written in hexadecimal.
BC_INTERIOR = 2
BC_WALL = 3
BC_PRESSURE_OUTLET = 5
BC_VELOCITY_INLET = 10

_BC_BY_ZONE = {
    "interior": BC_INTERIOR,
    "inlet": BC_VELOCITY_INLET,
    "outlet": BC_PRESSURE_OUTLET,
    "wall_bottom_mem": BC_WALL,
    "wall_top_mem": BC_WALL,
    "wall_spacer": BC_WALL,
}


def _linspace(start: float, stop: float, intervals: int) -> list[float]:
    if intervals < 1:
        raise ValueError(f"intervals must be >= 1, got {intervals}.")
    step = (stop - start) / intervals
    return [start + step * index for index in range(intervals + 1)]


class _Nodes:
    def __init__(self) -> None:
        self.xy: list[tuple[float, float]] = []
        self._index: dict[tuple[int, int], int] = {}

    def add(self, x_m: float, y_m: float) -> int:
        key = (round(x_m * 1.0e12), round(y_m * 1.0e12))
        found = self._index.get(key)
        if found is not None:
            return found
        found = len(self.xy)
        self._index[key] = found
        self.xy.append((float(x_m), float(y_m)))
        return found


def _quad_area(nodes: _Nodes, quad: tuple[int, int, int, int]) -> float:
    area = 0.0
    for index in range(4):
        x0, y0 = nodes.xy[quad[index]]
        x1, y1 = nodes.xy[quad[(index + 1) % 4]]
        area += x0 * y1 - x1 * y0
    return 0.5 * area


def _clockwise_square_loop(
    center_x: float,
    half_size: float,
    n_side: int,
) -> list[tuple[float, float]]:
    """Unique outer-square points, clockwise, starting at the top-right corner."""
    points: list[tuple[float, float]] = []
    top = half_size
    bottom = -half_size
    right = center_x + half_size
    left = center_x - half_size
    for index in range(n_side):
        fraction = index / n_side
        points.append((right, top - fraction * (top - bottom)))
    for index in range(n_side):
        fraction = index / n_side
        points.append((right - fraction * (right - left), bottom))
    for index in range(n_side):
        fraction = index / n_side
        points.append((left, bottom + fraction * (top - bottom)))
    for index in range(n_side):
        fraction = index / n_side
        points.append((left + fraction * (right - left), top))
    return points


def _radial_column(
    center: tuple[float, float],
    outer: tuple[float, float],
    radius_m: float,
    n_radial: int,
) -> list[tuple[float, float]]:
    vx = outer[0] - center[0]
    vy = outer[1] - center[1]
    outer_radius = math.hypot(vx, vy)
    if outer_radius <= radius_m:
        raise ValueError("O-grid outer radius is not outside the filament.")
    inner = (
        center[0] + radius_m * vx / outer_radius,
        center[1] + radius_m * vy / outer_radius,
    )
    column: list[tuple[float, float]] = []
    for index in range(n_radial + 1):
        fraction = index / n_radial
        column.append(
            (
                inner[0] + fraction * (outer[0] - inner[0]),
                inner[1] + fraction * (outer[1] - inner[1]),
            )
        )
    return column


def _add_block(
    nodes: _Nodes,
    quads: list[tuple[int, int, int, int]],
    xs: list[float],
    ys: list[float],
    fixed: dict[tuple[int, int], int],
) -> None:
    ids: list[list[int]] = []
    for iy, y_m in enumerate(ys):
        row: list[int] = []
        for ix, x_m in enumerate(xs):
            row.append(fixed.get((ix, iy), nodes.add(x_m, y_m)))
        ids.append(row)
    for iy in range(len(ys) - 1):
        for ix in range(len(xs) - 1):
            quads.append(
                (
                    ids[iy][ix],
                    ids[iy][ix + 1],
                    ids[iy + 1][ix + 1],
                    ids[iy + 1][ix],
                )
            )


def _side_ids(
    nodes: _Nodes,
    outer_ids: list[int],
    *,
    constant: str,
    value: float,
) -> list[int]:
    """Nodes on one square side, sorted along that side."""
    selected = []
    for node_id in outer_ids:
        x_m, y_m = nodes.xy[node_id]
        held = x_m if constant == "x" else y_m
        if abs(held - value) <= 1.0e-9:
            selected.append(node_id)
    if constant == "x":
        selected.sort(key=lambda node_id: nodes.xy[node_id][1])
    else:
        selected.sort(key=lambda node_id: nodes.xy[node_id][0])
    return selected


@dataclass
class QuadMesh:
    nodes: list[tuple[float, float]]
    quads: list[tuple[int, int, int, int]]
    boundaries: dict[str, list[tuple[int, int]]]
    interior: list[tuple[int, int, int, int]]
    max_size_m: float
    actual_max_edge_m: float
    actual_min_edge_m: float
    square_half_size_m: float
    n_side: int
    n_radial: int
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def n_cells(self) -> int:
        return len(self.quads)

    @property
    def n_nodes(self) -> int:
        return len(self.nodes)


def _classify_edges(
    nodes: _Nodes,
    quads: list[tuple[int, int, int, int]],
    *,
    length_m: float,
    half_height_m: float,
    obstacles: list[tuple[float, float, float]],
) -> tuple[dict[str, list[tuple[int, int]]], list[tuple[int, int, int, int]]]:
    directed: dict[tuple[int, int], int] = {}
    for quad_index, quad in enumerate(quads):
        for corner in range(4):
            edge = (quad[corner], quad[(corner + 1) % 4])
            if edge in directed:
                raise ValueError(f"Two quads share the directed edge {edge}.")
            directed[edge] = quad_index

    boundaries: dict[str, list[tuple[int, int]]] = {name: [] for name in _ZONE_NAMES}
    interior: list[tuple[int, int, int, int]] = []
    for edge, quad_index in directed.items():
        reverse = (edge[1], edge[0])
        if reverse in directed:
            if edge[0] < edge[1]:
                interior.append((edge[0], edge[1], quad_index, directed[reverse]))
            continue
        boundaries[_boundary_name(nodes, edge, length_m, half_height_m, obstacles)].append(
            edge
        )
    return boundaries, interior


def _boundary_name(
    nodes: _Nodes,
    edge: tuple[int, int],
    length_m: float,
    half_height_m: float,
    obstacles: list[tuple[float, float, float]],
) -> str:
    points = (nodes.xy[edge[0]], nodes.xy[edge[1]])
    if all(abs(point[0]) <= 1.0e-9 for point in points):
        return "inlet"
    if all(abs(point[0] - length_m) <= 1.0e-9 for point in points):
        return "outlet"
    if all(abs(point[1] + half_height_m) <= 1.0e-9 for point in points):
        return "wall_bottom_mem"
    if all(abs(point[1] - half_height_m) <= 1.0e-9 for point in points):
        return "wall_top_mem"
    for x_m, y_m in points:
        if not any(
            abs(math.hypot(x_m - center_x, y_m - center_y) - radius_m) <= 1.0e-8
            for center_x, center_y, radius_m in obstacles
        ):
            raise ValueError(
                "A boundary edge is not on the inlet, outlet, membranes, "
                f"or filament: {points!r}."
            )
    return "wall_spacer"


def _edge_lengths(nodes: _Nodes, quads: list[tuple[int, int, int, int]]) -> tuple[float, float]:
    shortest = math.inf
    longest = 0.0
    for quad in quads:
        for corner in range(4):
            x0, y0 = nodes.xy[quad[corner]]
            x1, y1 = nodes.xy[quad[(corner + 1) % 4]]
            length = math.hypot(x1 - x0, y1 - y0)
            shortest = min(shortest, length)
            longest = max(longest, length)
    return shortest, longest


def build_quad_mesh(config: PilotConfig) -> QuadMesh:
    """Build the fluid quad mesh for ``config``. Raises if a quad is inverted."""
    layout = resolution_layout(config)
    max_size_m = float(layout["max_size_m"])
    radius_m = 0.5 * config.d_m
    half_height_m = 0.5 * config.channel_height_m
    square_half = float(layout["square_half_size_m"])
    n_side = int(layout["n_side"])
    n_radial = int(layout["n_radial"])
    n_gap = int(layout["n_gap"])
    n_stream = int(layout["n_stream"])

    nodes = _Nodes()
    quads: list[tuple[int, int, int, int]] = []
    obstacles: list[tuple[float, float, float]] = []
    gap_bottom = _linspace(-half_height_m, -square_half, n_gap)
    gap_top = _linspace(square_half, half_height_m, n_gap)

    for pitch in range(config.n_pitches):
        x0 = pitch * config.L_m
        x1 = (pitch + 1) * config.L_m
        center_x = (pitch + 0.5) * config.L_m
        center = (center_x, 0.0)
        obstacles.append((center_x, 0.0, radius_m))
        outer_points = _clockwise_square_loop(center_x, square_half, n_side)
        columns = [
            _radial_column(center, point, radius_m, n_radial)
            for point in outer_points
        ]
        column_ids: list[list[int]] = []
        for column in columns:
            column_ids.append([nodes.add(x_m, y_m) for x_m, y_m in column])
        n_loop = len(column_ids)
        for index in range(n_loop):
            nxt = (index + 1) % n_loop
            for layer in range(n_radial):
                quads.append(
                    (
                        column_ids[index][layer],
                        column_ids[nxt][layer],
                        column_ids[nxt][layer + 1],
                        column_ids[index][layer + 1],
                    )
                )
        outer_ids = [column[-1] for column in column_ids]
        left_ids = _side_ids(
            nodes, outer_ids, constant="x", value=center_x - square_half
        )
        right_ids = _side_ids(
            nodes, outer_ids, constant="x", value=center_x + square_half
        )
        bottom_ids = _side_ids(nodes, outer_ids, constant="y", value=-square_half)
        top_ids = _side_ids(nodes, outer_ids, constant="y", value=square_half)
        if (
            len(left_ids) != n_side + 1
            or len(right_ids) != n_side + 1
            or len(bottom_ids) != n_side + 1
            or len(top_ids) != n_side + 1
        ):
            raise ValueError("O-grid square sides do not have the expected node count.")

        square_ys = [nodes.xy[node_id][1] for node_id in left_ids]
        full_ys = gap_bottom[:-1] + square_ys + gap_top[1:]
        left_by_y = {
            round(nodes.xy[node_id][1] * 1.0e12): node_id for node_id in left_ids
        }
        right_by_y = {
            round(nodes.xy[node_id][1] * 1.0e12): node_id for node_id in right_ids
        }
        upstream_xs = _linspace(x0, center_x - square_half, n_stream)
        upstream_fixed = {
            (len(upstream_xs) - 1, iy): left_by_y[round(y_m * 1.0e12)]
            for iy, y_m in enumerate(full_ys)
            if round(y_m * 1.0e12) in left_by_y
        }
        _add_block(nodes, quads, upstream_xs, full_ys, upstream_fixed)

        downstream_xs = _linspace(center_x + square_half, x1, n_stream)
        downstream_fixed = {
            (0, iy): right_by_y[round(y_m * 1.0e12)]
            for iy, y_m in enumerate(full_ys)
            if round(y_m * 1.0e12) in right_by_y
        }
        _add_block(nodes, quads, downstream_xs, full_ys, downstream_fixed)

        top_xs = [nodes.xy[node_id][0] for node_id in top_ids]
        top_by_x = {
            round(nodes.xy[node_id][0] * 1.0e12): node_id for node_id in top_ids
        }
        above_fixed = {
            (ix, 0): top_by_x[round(x_m * 1.0e12)] for ix, x_m in enumerate(top_xs)
        }
        _add_block(nodes, quads, top_xs, gap_top, above_fixed)

        bottom_xs = [nodes.xy[node_id][0] for node_id in bottom_ids]
        bottom_by_x = {
            round(nodes.xy[node_id][0] * 1.0e12): node_id for node_id in bottom_ids
        }
        below_fixed = {
            (ix, len(gap_bottom) - 1): bottom_by_x[round(x_m * 1.0e12)]
            for ix, x_m in enumerate(bottom_xs)
        }
        _add_block(nodes, quads, bottom_xs, gap_bottom, below_fixed)

    for quad in quads:
        if _quad_area(nodes, quad) <= 0.0:
            raise ValueError("Quad mesh contains a non-positive area.")

    length_m = config.n_pitches * config.L_m
    boundaries, interior = _classify_edges(
        nodes,
        quads,
        length_m=length_m,
        half_height_m=half_height_m,
        obstacles=obstacles,
    )
    shortest, longest = _edge_lengths(nodes, quads)
    return QuadMesh(
        nodes=nodes.xy,
        quads=quads,
        boundaries=boundaries,
        interior=interior,
        max_size_m=max_size_m,
        actual_max_edge_m=longest,
        actual_min_edge_m=shortest,
        square_half_size_m=square_half,
        n_side=n_side,
        n_radial=n_radial,
        counts={name: len(edges) for name, edges in boundaries.items()},
    )


def _hex(value: int) -> str:
    return format(value, "x")


def write_fluent_msh(mesh: QuadMesh, path) -> None:
    """Write a 2D ASCII Fluent mesh. Indices in the headers are hexadecimal."""
    from pathlib import Path

    destination = Path(path)
    n_nodes = mesh.n_nodes
    n_cells = mesh.n_cells
    owner: dict[tuple[int, int], int] = {}
    for quad_index, quad in enumerate(mesh.quads):
        for corner in range(4):
            owner[(quad[corner], quad[(corner + 1) % 4])] = quad_index
    face_groups: list[tuple[str, int, list[tuple[int, int, int, int]]]] = [
        (
            "interior",
            BC_INTERIOR,
            [
                (first + 1, second + 1, c0 + 1, c1 + 1)
                for first, second, c0, c1 in mesh.interior
            ],
        )
    ]
    for name in _ZONE_NAMES:
        faces = []
        for first, second in mesh.boundaries[name]:
            faces.append((first + 1, second + 1, owner[(first, second)] + 1, 0))
        face_groups.append((name, _BC_BY_ZONE[name], faces))

    lines: list[str] = [
        '(0 "ro_2d_pilot algebraic O-grid")',
        "(2 2)",
        f"(10 (0 1 {_hex(n_nodes)} 0 2))",
        f"(10 (1 1 {_hex(n_nodes)} 1 2)(",
    ]
    for x_m, y_m in mesh.nodes:
        lines.append(f"{x_m:.12e} {y_m:.12e}")
    lines.append("))")
    lines.append(f"(12 (0 1 {_hex(n_cells)} 0))")
    lines.append(f"(12 (2 1 {_hex(n_cells)} 1 3))")
    n_faces = sum(len(faces) for _, _, faces in face_groups)
    lines.append(f"(13 (0 1 {_hex(n_faces)} 0))")
    cursor = 1
    zone_ids = {"interior": 3}
    next_zone = 4
    for name, _bc, _faces in face_groups:
        if name == "interior":
            continue
        zone_ids[name] = next_zone
        next_zone += 1
    for name, bc_type, faces in face_groups:
        if not faces:
            raise ValueError(f"Mesh zone {name} has no faces.")
        zone_id = 3 if name == "interior" else zone_ids[name]
        last = cursor + len(faces) - 1
        lines.append(
            f"(13 ({_hex(zone_id)} {_hex(cursor)} {_hex(last)} "
            f"{_hex(bc_type)} 2)("
        )
        for n0, n1, c0, c1 in faces:
            lines.append(f"{_hex(n0)} {_hex(n1)} {_hex(c0)} {_hex(c1)}")
        lines.append("))")
        cursor = last + 1
    lines.append('(39 (2 fluid fluid)())')
    lines.append('(39 (3 interior interior)())')
    lines.append(f'(39 ({_hex(zone_ids["inlet"])} velocity-inlet inlet)())')
    lines.append(f'(39 ({_hex(zone_ids["outlet"])} pressure-outlet outlet)())')
    lines.append(
        f'(39 ({_hex(zone_ids["wall_bottom_mem"])} wall wall_bottom_mem)())'
    )
    lines.append(f'(39 ({_hex(zone_ids["wall_top_mem"])} wall wall_top_mem)())')
    lines.append(f'(39 ({_hex(zone_ids["wall_spacer"])} wall wall_spacer)())')
    lines.append("")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines), encoding="ascii")

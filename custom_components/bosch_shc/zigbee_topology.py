"""Build a Zigbee mesh topology graph from SHCZigbeeRoutingCoordinator data.

The SHC's undocumented ``GET /smarthome/zigbee/routinginfo/{deviceId}``
endpoint (see boschshcpy.zigbee_routing) reports, per device, its own hop
chain back to the controller — not a global neighbor/routing table like a
Zigbee coordinator's Mgmt_Lqi_req scan (Zigbee2MQTT/ZHA). Each polled device
is therefore the authoritative source for exactly one outgoing edge: its own
first hop. Stitching every device's first hop together yields a tree rooted
at the controller, without double-counting or conflicting quality values for
a shared upstream link.

There is no numeric LQI/RSSI here, only the SHC's own categorical quality
(good/medium/bad/...) — a structural limit of the API, not of this module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from boschshcpy.zigbee_routing import SHCZigbeeRoutingInfo

CONTROLLER_NODE_ID = "controller"

# Quality -> display color, shared by the SVG and Mermaid renderers.
_QUALITY_COLOR = {
    "good": "#2e7d32",
    "medium": "#f9a825",
    "bad": "#c62828",
    "no_connection": "#9e9e9e",
    "device_not_initialized": "#9e9e9e",
    "not_supported": "#9e9e9e",
    "unknown": "#9e9e9e",
}


def build_topology_graph(
    routing_data: dict[str, "SHCZigbeeRoutingInfo"],
    device_names: dict[str, str],
    controller_name: str,
) -> dict[str, Any]:
    """Build a JSON-serializable node/edge graph from coordinator data.

    Each device in ``routing_data`` contributes at most one outgoing edge:
    its own reported first hop (route[0] -> route[1], or -> the controller
    if it connects directly). Devices with an empty route (no connection)
    become an unconnected node with no edge.
    """
    node_ids: set[str] = {CONTROLLER_NODE_ID}
    edges: list[dict[str, Any]] = []

    for device_id, info in routing_data.items():
        node_ids.add(device_id)
        route = info.route
        if not route:
            continue
        target_id = route[1].device_id if len(route) > 1 else CONTROLLER_NODE_ID
        node_ids.add(target_id)
        edges.append(
            {
                "from": device_id,
                "to": target_id,
                "quality": route[0].quality.value.lower(),
            }
        )

    nodes = [
        {
            "id": node_id,
            "name": controller_name
            if node_id == CONTROLLER_NODE_ID
            else device_names.get(node_id, node_id),
        }
        for node_id in sorted(node_ids)
    ]

    return {"nodes": nodes, "edges": edges}


def topology_to_mermaid(graph: dict[str, Any]) -> str:
    """Render the graph as Mermaid flowchart text.

    Paste the result into https://mermaid.live, a GitHub/GitLab markdown
    file, Obsidian, or any Mermaid-capable renderer to view it as a diagram.
    """
    lines = ["graph TD"]

    def _mermaid_id(node_id: str) -> str:
        # Mermaid node ids must not contain ':' or other punctuation used in
        # Bosch device ids (e.g. "hdm:ZigBee:0123...").
        return "n" + "".join(c if c.isalnum() else "_" for c in node_id)

    for node in graph["nodes"]:
        label = node["name"].replace('"', "'")
        lines.append(f'    {_mermaid_id(node["id"])}["{label}"]')

    for index, edge in enumerate(graph["edges"]):
        quality = edge["quality"]
        color = _QUALITY_COLOR.get(quality, _QUALITY_COLOR["unknown"])
        source = _mermaid_id(edge["from"])
        target = _mermaid_id(edge["to"])
        lines.append(f"    {source} -->|{quality}| {target}")
        lines.append(f"    linkStyle {index} stroke:{color},stroke-width:2px")

    return "\n".join(lines)


def _esc_html(text: str) -> str:
    """Escape text for safe interpolation into HTML/SVG markup."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def topology_to_svg(graph: dict[str, Any], width: int = 900) -> str:
    """Render the graph as a self-contained hierarchical-tree SVG.

    Pure-Python layout (no new dependency, no external JS/CDN): breadth-first
    layering from the controller, nodes spread evenly within their layer.
    Since build_topology_graph produces a tree (each node has at most one
    outgoing edge), a simple layered layout is sufficient — there are no
    cycles or cross-links to route around.
    """
    children: dict[str, list[str]] = {}
    for edge in graph["edges"]:
        children.setdefault(edge["to"], []).append(edge["from"])
    names_by_id = {node["id"]: node["name"] for node in graph["nodes"]}
    connected_ids = {CONTROLLER_NODE_ID}
    for edge in graph["edges"]:
        connected_ids.add(edge["from"])
        connected_ids.add(edge["to"])

    layers: list[list[str]] = [[CONTROLLER_NODE_ID]]
    seen = {CONTROLLER_NODE_ID}
    while True:
        next_layer = [
            child
            for parent in layers[-1]
            for child in children.get(parent, [])
            if child not in seen
        ]
        if not next_layer:
            break
        seen.update(next_layer)
        layers.append(next_layer)

    unconnected = [node_id for node_id in names_by_id if node_id not in connected_ids]
    if unconnected:
        layers.append(unconnected)

    row_height = 90
    height = max(200, row_height * len(layers) + 60)
    positions: dict[str, tuple[float, float]] = {}
    for depth, layer in enumerate(layers):
        y = 40 + depth * row_height
        step = width / (len(layer) + 1)
        for index, node_id in enumerate(layer, start=1):
            positions[node_id] = (step * index, y)

    svg_parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'font-family="sans-serif" font-size="12">',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
    ]

    for edge in graph["edges"]:
        if edge["from"] not in positions or edge["to"] not in positions:
            continue
        x1, y1 = positions[edge["from"]]
        x2, y2 = positions[edge["to"]]
        color = _QUALITY_COLOR.get(edge["quality"], _QUALITY_COLOR["unknown"])
        svg_parts.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{color}" stroke-width="2"/>'
        )

    for node_id, (node_x, node_y) in positions.items():
        label = _esc_html(names_by_id.get(node_id, node_id))
        is_controller = node_id == CONTROLLER_NODE_ID
        fill = "#1565c0" if is_controller else "#37474f"
        svg_parts.append(
            f'<circle cx="{node_x:.1f}" cy="{node_y:.1f}" r="6" fill="{fill}"/>'
        )
        svg_parts.append(
            f'<text x="{node_x:.1f}" y="{node_y - 10:.1f}" text-anchor="middle" '
            f'fill="#212121">{label}</text>'
        )

    svg_parts.append("</svg>")
    return "".join(svg_parts)


def topology_to_html(graph: dict[str, Any], title: str) -> str:
    """Wrap the SVG in a minimal, self-contained, offline-safe HTML page.

    ``title`` is derived from the config-entry title, which a user can set
    to arbitrary text — escape it like every other untrusted string here.
    """
    svg = topology_to_svg(graph)
    safe_title = _esc_html(title)
    legend_items = "".join(
        f'<span style="color:{color}">&#9679;</span> {quality} &nbsp;'
        for quality, color in _QUALITY_COLOR.items()
    )
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        f"<title>{safe_title}</title></head><body>"
        f"<h1>{safe_title}</h1>"
        f"<p>{legend_items}</p>"
        f"{svg}"
        "</body></html>"
    )

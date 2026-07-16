"""Build a Zigbee mesh topology graph from SHCZigbeeRoutingCoordinator data.

The SHC's undocumented ``GET /smarthome/zigbee/routinginfo/{deviceId}``
endpoint (see boschshcpy.zigbee_routing) reports, per device, its own hop
chain back to the controller — not a global neighbor/routing table like a
Zigbee coordinator's Mgmt_Lqi_req scan (Zigbee2MQTT/ZHA). There is no numeric
LQI/RSSI here, only the SHC's own categorical quality (good/medium/bad/...) —
a structural limit of the API, not of this module.

Each device's own first hop (route[0] -> route[1]) is the authoritative
source for its own outgoing edge. But a device's full route also names every
hop between it and the controller — including routers that don't answer
their own routinginfo query (excluded, offline, or simply never polled).
Those intermediate hops are otherwise invisible, so every consecutive pair
in every device's full route is used to fill in an edge for a hop that has
no self-reported one of its own. A self-reported edge always wins over an
inferred one for the same source node — see ``_add_edge``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from boschshcpy.zigbee_routing import SHCZigbeeRoutingInfo

CONTROLLER_NODE_ID = "controller"

# Fixed status palette, validated for >=3:1 contrast on light and dark alike.
# Anything else is "no data" — rendered via the theme-aware neutral token.
_STATUS_COLOR = {
    "good": "#0ca30c",
    "medium": "#fab219",
    "bad": "#d03b3b",
}
_NEUTRAL_VAR = "var(--node-neutral)"  # SVG: adapts to light/dark
_NEUTRAL_MERMAID = "#8a8a86"  # Mermaid has no CSS vars — fixed mid-gray


def _svg_quality_color(quality: str) -> str:
    return _STATUS_COLOR.get(quality, _NEUTRAL_VAR)


def _mermaid_quality_color(quality: str) -> str:
    return _STATUS_COLOR.get(quality, _NEUTRAL_MERMAID)


def build_topology_graph(
    routing_data: dict[str, "SHCZigbeeRoutingInfo"],
    device_names: dict[str, str],
    controller_name: str,
    all_zigbee_device_ids: "set[str] | None" = None,
) -> dict[str, Any]:
    """Build a JSON-serializable node/edge graph from coordinator data.

    Every device contributes at most one outgoing edge — its own reported
    first hop (route[0] -> route[1], or -> the controller if it connects
    directly) when it has one. Additionally, every consecutive pair in every
    device's *full* route fills in an edge for an intermediate hop that has
    no self-reported entry of its own, so a router that doesn't answer its
    own routinginfo query still shows up connected. Devices with an empty
    route (no connection) become an unconnected node with no edge.

    ``all_zigbee_device_ids``, when given, seeds every known Zigbee device as
    a node up front -- otherwise a device whose on-demand routing query
    failed every poll (e.g. a sleepy battery end device that just never
    answered in time) is silently missing from the graph entirely, with no
    indication it exists (reported live: some motion detectors/Twinguards/
    window contacts never showed up at all).
    """
    node_ids: set[str] = {CONTROLLER_NODE_ID, *(all_zigbee_device_ids or ())}
    edges_by_source: dict[str, dict[str, Any]] = {}

    def _add_edge(source: str, target: str, quality: str) -> None:
        node_ids.add(source)
        node_ids.add(target)
        # First writer wins: pass 1 (self-reported) runs before pass 2
        # (inferred), so a self-report is never overwritten by an inference.
        edges_by_source.setdefault(
            source, {"from": source, "to": target, "quality": quality}
        )

    # Pass 1: each device's own first hop.
    for device_id, info in routing_data.items():
        node_ids.add(device_id)
        route = info.route
        if not route:
            continue
        target_id = route[1].device_id if len(route) > 1 else CONTROLLER_NODE_ID
        _add_edge(device_id, target_id, route[0].quality.value.lower())

    # Pass 2: fill gaps from every other hop in every device's full route.
    for info in routing_data.values():
        route = info.route
        for i in range(1, len(route)):
            source_id = route[i].device_id
            target_id = (
                route[i + 1].device_id if i + 1 < len(route) else CONTROLLER_NODE_ID
            )
            _add_edge(source_id, target_id, route[i].quality.value.lower())

    nodes = [
        {
            "id": node_id,
            "name": controller_name
            if node_id == CONTROLLER_NODE_ID
            else device_names.get(node_id, node_id),
        }
        for node_id in sorted(node_ids)
    ]

    return {"nodes": nodes, "edges": list(edges_by_source.values())}


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
        color = _mermaid_quality_color(quality)
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


# Rough monospace-ish width estimate for a label chip — SVG can't measure
# rendered text server-side, so this is a heuristic, not exact metrics.
_CHAR_WIDTH_PX = 6.6
_CHIP_PAD_X = 8
_CHIP_HEIGHT = 20


def topology_to_svg(graph: dict[str, Any], width: int = 900) -> str:
    """Render the graph as a self-contained hierarchical-tree SVG.

    Pure-Python layout (no new dependency, no external JS/CDN): breadth-first
    layering from the controller, nodes spread evenly within their layer.
    Node dots carry a hover tooltip (native ``<title>``, no JS) with the
    device id and, per edge, the link quality.
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

    row_height = 96
    height = max(200, row_height * len(layers) + 60)
    positions: dict[str, tuple[float, float]] = {}
    for depth, layer in enumerate(layers):
        y = 46 + depth * row_height
        step = width / (len(layer) + 1)
        for index, node_id in enumerate(layer, start=1):
            positions[node_id] = (step * index, y)

    svg_parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'font-family="sans-serif" font-size="12">',
        f'<rect width="{width}" height="{height}" fill="var(--surface)"/>',
    ]

    for edge in graph["edges"]:
        if edge["from"] not in positions or edge["to"] not in positions:
            continue
        x1, y1 = positions[edge["from"]]
        x2, y2 = positions[edge["to"]]
        color = _svg_quality_color(edge["quality"])
        from_name = _esc_html(names_by_id.get(edge["from"], edge["from"]))
        to_name = _esc_html(names_by_id.get(edge["to"], edge["to"]))
        svg_parts.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{color}" stroke-width="2" stroke-linecap="round">'
            f"<title>{from_name} → {to_name}: {edge['quality']}</title>"
            "</line>"
        )

    for node_id, (node_x, node_y) in positions.items():
        label = _esc_html(names_by_id.get(node_id, node_id))
        is_controller = node_id == CONTROLLER_NODE_ID
        dot_fill = "var(--node-controller)" if is_controller else "var(--node-default)"

        chip_width = len(names_by_id.get(node_id, node_id)) * _CHAR_WIDTH_PX + (
            2 * _CHIP_PAD_X
        )
        chip_x = node_x - chip_width / 2
        chip_y = node_y - 10 - _CHIP_HEIGHT

        svg_parts.append(
            f'<rect x="{chip_x:.1f}" y="{chip_y:.1f}" width="{chip_width:.1f}" '
            f'height="{_CHIP_HEIGHT}" rx="5" fill="var(--surface-card)"/>'
        )
        svg_parts.append(
            f'<text x="{node_x:.1f}" y="{chip_y + _CHIP_HEIGHT - 6:.1f}" '
            f'text-anchor="middle" fill="var(--text-primary)">{label}</text>'
        )
        svg_parts.append(
            f'<circle cx="{node_x:.1f}" cy="{node_y:.1f}" r="6" fill="{dot_fill}" '
            f'stroke="var(--surface)" stroke-width="2">'
            f"<title>{label}</title></circle>"
        )

    svg_parts.append("</svg>")
    return "".join(svg_parts)


def topology_to_html(graph: dict[str, Any], title: str) -> str:
    """Wrap the SVG in a minimal, self-contained, offline-safe HTML page.

    ``title`` is derived from the config-entry title, which a user can set
    to arbitrary text — escape it like every other untrusted string here.
    Theme-aware: honors the OS's light/dark preference via
    ``prefers-color-scheme`` (the status colors themselves stay fixed —
    validated against both light and dark surfaces, see ``_STATUS_COLOR``).
    """
    svg = topology_to_svg(graph)
    safe_title = _esc_html(title)
    legend_items = "".join(
        f'<span class="swatch" style="background:{color}"></span>{quality}'
        for quality, color in {
            **_STATUS_COLOR,
            "no_connection / unknown": _NEUTRAL_MERMAID,
        }.items()
    )
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        f"<title>{safe_title}</title>"
        "<style>"
        ":root{color-scheme:light dark}"
        "body{"
        "--surface:#fcfcfb;--surface-card:#f0efec;"
        "--text-primary:#0b0b0b;--text-secondary:#52514e;"
        "--node-controller:#2a78d6;--node-default:#52514e;--node-neutral:#8a8a86;"
        "margin:0;padding:24px;background:var(--surface);color:var(--text-primary);"
        "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}"
        "@media (prefers-color-scheme:dark){body{"
        "--surface:#1a1a19;--surface-card:#262625;"
        "--text-primary:#ffffff;--text-secondary:#c3c2b7;"
        "--node-controller:#3987e5;--node-default:#c3c2b7;--node-neutral:#8a8a86"
        "}}"
        "h1{font-size:1.25rem;margin:0 0 4px}"
        ".legend{color:var(--text-secondary);font-size:0.85rem;"
        "display:flex;gap:16px;flex-wrap:wrap;margin:0 0 16px}"
        ".legend .swatch{display:inline-block;width:10px;height:10px;"
        "border-radius:50%;margin-right:6px;vertical-align:middle}"
        "svg{max-width:100%;height:auto;border-radius:8px}"
        "</style></head><body>"
        f"<h1>{safe_title}</h1>"
        f'<p class="legend">{legend_items}</p>'
        f"{svg}"
        "</body></html>"
    )

"""Unit tests for zigbee_topology.py.

Pure-logic tests, no HA boilerplate needed (mirrors test_coordinator.py):
build_topology_graph/topology_to_mermaid/topology_to_svg/topology_to_html
only take plain data in and strings/dicts out.
"""

from __future__ import annotations

from boschshcpy.zigbee_routing import SHCZigbeeRoutingInfo

from custom_components.bosch_shc.zigbee_topology import (
    CONTROLLER_NODE_ID,
    build_topology_graph,
    topology_to_html,
    topology_to_mermaid,
    topology_to_svg,
)


def _routing_info(device_id: str, route: list[tuple[str, str]]) -> SHCZigbeeRoutingInfo:
    """Build an SHCZigbeeRoutingInfo like the real GET .../routinginfo/{id} response."""
    return SHCZigbeeRoutingInfo(
        {
            "device": device_id,
            "aggregatedQuality": route[0][1] if route else "NO_CONNECTION",
            "route": [{"deviceId": did, "quality": q} for did, q in route],
        }
    )


def test_direct_connection_single_hop_edge_to_controller() -> None:
    """A device connected directly to the controller: route has one entry."""
    routing_data = {
        "hdm:ZigBee:aaa": _routing_info("hdm:ZigBee:aaa", [("hdm:ZigBee:aaa", "GOOD")]),
    }
    graph = build_topology_graph(
        routing_data, {"hdm:ZigBee:aaa": "Plug A"}, controller_name="SHC"
    )

    assert graph["edges"] == [
        {"from": "hdm:ZigBee:aaa", "to": CONTROLLER_NODE_ID, "quality": "good"}
    ]
    node_ids = {n["id"] for n in graph["nodes"]}
    assert node_ids == {"hdm:ZigBee:aaa", CONTROLLER_NODE_ID}


def test_multi_hop_route_builds_chain_of_edges() -> None:
    """A 3-hop route contributes exactly one authoritative edge: its own first hop."""
    routing_data = {
        "hdm:ZigBee:leaf": _routing_info(
            "hdm:ZigBee:leaf",
            [
                ("hdm:ZigBee:leaf", "MEDIUM"),
                ("hdm:ZigBee:router1", "GOOD"),
            ],
        ),
        "hdm:ZigBee:router1": _routing_info(
            "hdm:ZigBee:router1", [("hdm:ZigBee:router1", "GOOD")]
        ),
    }
    graph = build_topology_graph(
        routing_data,
        {"hdm:ZigBee:leaf": "Leaf", "hdm:ZigBee:router1": "Router 1"},
        controller_name="SHC",
    )

    assert {"from": "hdm:ZigBee:leaf", "to": "hdm:ZigBee:router1", "quality": "medium"} in (
        graph["edges"]
    )
    assert {
        "from": "hdm:ZigBee:router1",
        "to": CONTROLLER_NODE_ID,
        "quality": "good",
    } in graph["edges"]
    # Exactly one edge per polled device — router1's own self-reported edge
    # wins over the same hop inferred from the leaf's longer route, so
    # there's no duplicate.
    assert len(graph["edges"]) == 2


def test_intermediate_hop_with_no_own_entry_is_filled_from_a_longer_route() -> None:
    """A router that never answers its own routinginfo query (excluded,
    offline, or just never separately polled) is otherwise invisible — but
    still shows up connected if some other device's full route passes
    through it."""
    routing_data = {
        "hdm:ZigBee:leaf": _routing_info(
            "hdm:ZigBee:leaf",
            [
                ("hdm:ZigBee:leaf", "MEDIUM"),
                ("hdm:ZigBee:ghost_router", "GOOD"),
            ],
        ),
        # Note: "hdm:ZigBee:ghost_router" has no entry of its own here.
    }
    graph = build_topology_graph(
        routing_data,
        {"hdm:ZigBee:leaf": "Leaf", "hdm:ZigBee:ghost_router": "Ghost Router"},
        controller_name="SHC",
    )

    assert {
        "from": "hdm:ZigBee:leaf",
        "to": "hdm:ZigBee:ghost_router",
        "quality": "medium",
    } in graph["edges"]
    # Inferred from the leaf's own full route: the ghost router's onward
    # hop to the controller, quality carried on its own route entry.
    assert {
        "from": "hdm:ZigBee:ghost_router",
        "to": CONTROLLER_NODE_ID,
        "quality": "good",
    } in graph["edges"]
    assert len(graph["edges"]) == 2


def test_no_connection_device_has_no_edge_but_is_still_a_node() -> None:
    """An offline device (empty route) must not silently vanish from the graph."""
    routing_data = {
        "hdm:ZigBee:offline": _routing_info("hdm:ZigBee:offline", []),
    }
    graph = build_topology_graph(
        routing_data, {"hdm:ZigBee:offline": "Offline Sensor"}, controller_name="SHC"
    )

    assert graph["edges"] == []
    assert any(n["id"] == "hdm:ZigBee:offline" for n in graph["nodes"])


def test_device_with_no_routing_data_still_shows_as_a_node() -> None:
    """Field report: some devices (sleepy end devices whose on-demand
    routinginfo query never answered in time) were silently missing from
    the graph entirely, with no indication they even exist. When the caller
    passes the full known-Zigbee-device set, every one of them must appear
    as a node even if routing_data has no entry for it at all."""
    routing_data: dict[str, SHCZigbeeRoutingInfo] = {}
    graph = build_topology_graph(
        routing_data,
        {"hdm:ZigBee:sleepy": "Sleepy Motion Sensor"},
        controller_name="SHC",
        all_zigbee_device_ids={"hdm:ZigBee:sleepy"},
    )

    assert graph["edges"] == []
    assert any(n["id"] == "hdm:ZigBee:sleepy" for n in graph["nodes"])


def test_devices_with_routing_data_are_unaffected_by_the_full_id_set() -> None:
    """all_zigbee_device_ids must not duplicate or otherwise disturb nodes
    that DO have real routing data."""
    routing_data = {
        "hdm:ZigBee:a": _routing_info("hdm:ZigBee:a", [("hdm:ZigBee:a", "GOOD")]),
    }
    graph = build_topology_graph(
        routing_data,
        {"hdm:ZigBee:a": "Device A"},
        controller_name="SHC",
        all_zigbee_device_ids={"hdm:ZigBee:a"},
    )

    node_ids = [n["id"] for n in graph["nodes"]]
    assert node_ids.count("hdm:ZigBee:a") == 1
    assert graph["edges"] == [
        {"from": "hdm:ZigBee:a", "to": "controller", "quality": "good"}
    ]


def test_unknown_device_name_falls_back_to_device_id() -> None:
    """A device missing from device_names still renders (id as its own label)."""
    routing_data = {
        "hdm:ZigBee:mystery": _routing_info(
            "hdm:ZigBee:mystery", [("hdm:ZigBee:mystery", "BAD")]
        ),
    }
    graph = build_topology_graph(routing_data, {}, controller_name="SHC")

    node = next(n for n in graph["nodes"] if n["id"] == "hdm:ZigBee:mystery")
    assert node["name"] == "hdm:ZigBee:mystery"


def test_mermaid_output_contains_every_node_and_edge() -> None:
    routing_data = {
        "hdm:ZigBee:aaa": _routing_info("hdm:ZigBee:aaa", [("hdm:ZigBee:aaa", "GOOD")]),
    }
    graph = build_topology_graph(
        routing_data, {"hdm:ZigBee:aaa": "Plug A"}, controller_name="SHC"
    )
    mermaid = topology_to_mermaid(graph)

    assert mermaid.startswith("graph TD")
    assert "Plug A" in mermaid
    assert "SHC" in mermaid
    assert "good" in mermaid


def test_mermaid_sanitizes_colons_in_device_ids() -> None:
    """Bosch device ids contain ':' which is not valid inside a Mermaid node id."""
    routing_data = {
        "hdm:ZigBee:aaa": _routing_info("hdm:ZigBee:aaa", [("hdm:ZigBee:aaa", "GOOD")]),
    }
    graph = build_topology_graph(routing_data, {}, controller_name="SHC")
    mermaid = topology_to_mermaid(graph)

    assert ":" not in mermaid.split("\n")[1].split("[")[0]


def test_svg_renders_a_node_per_device_and_line_per_edge() -> None:
    routing_data = {
        "hdm:ZigBee:aaa": _routing_info("hdm:ZigBee:aaa", [("hdm:ZigBee:aaa", "GOOD")]),
        "hdm:ZigBee:offline": _routing_info("hdm:ZigBee:offline", []),
    }
    graph = build_topology_graph(
        routing_data,
        {"hdm:ZigBee:aaa": "Plug A", "hdm:ZigBee:offline": "Offline"},
        controller_name="SHC",
    )
    svg = topology_to_svg(graph)

    assert svg.startswith("<svg")
    assert svg.count("<circle") == 3  # controller + Plug A + Offline
    assert svg.count("<line") == 1  # only the one real edge
    assert "Offline" in svg  # unconnected node still rendered, not dropped


def test_svg_handles_empty_graph_without_crashing() -> None:
    graph = build_topology_graph({}, {}, controller_name="SHC")
    svg = topology_to_svg(graph)

    assert svg.startswith("<svg")
    assert svg.count("<circle") == 1  # just the controller


def test_html_wraps_svg_with_legend_and_title() -> None:
    graph = build_topology_graph({}, {}, controller_name="SHC")
    html = topology_to_html(graph, "Zigbee topology — Test SHC")

    assert html.startswith("<!doctype html>")
    assert "Zigbee topology" in html
    assert "<svg" in html
    assert "good" in html  # legend lists every quality level


def test_html_escapes_title_from_untrusted_config_entry_name() -> None:
    """The title comes from the config entry (user-settable) — must not be
    injectable HTML, unlike node labels this must also be escaped."""
    graph = build_topology_graph({}, {}, controller_name="SHC")
    html = topology_to_html(graph, "<script>alert(1)</script>")

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html

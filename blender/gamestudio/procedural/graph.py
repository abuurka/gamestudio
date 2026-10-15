# SPDX-License-Identifier: GPL-3.0-or-later
"""A small DSL for writing geometry-node groups in Python.

    g = Graph.new("GS Example", [Input("density", "Density", "FLOAT", 1.0, 0.0, 10.0, "...")])
    pts = g.node("GeometryNodeDistributePointsOnFaces", {"Mesh": g.input("Surface"),
                 "Density Max": g.input("Density")}, distribute_method="POISSON")
    g.output(pts.outputs["Points"])

node(kind, inputs, **props) sets the node's properties first (data_type, operation, ...), so the
sockets that depend on them exist, then fills `inputs`: a dict by socket name (first available
socket with that name) or a list by position among the available sockets. A value that is a socket
is linked; anything else becomes the socket's default value. Helpers (math, vec, xyz, cmp, switch,
random, capture, ...) return output sockets, so expressions read like formulas.
"""

import math
from typing import NamedTuple

import bpy

SOCKET_TYPES = {
    "FLOAT": "NodeSocketFloat", "INT": "NodeSocketInt", "BOOL": "NodeSocketBool",
    "VECTOR": "NodeSocketVector", "OBJECT": "NodeSocketObject", "COLLECTION": "NodeSocketCollection",
    "MATERIAL": "NodeSocketMaterial", "STRING": "NodeSocketString", "MENU": "NodeSocketMenu",
    "GEOMETRY": "NodeSocketGeometry",
}


class Input(NamedTuple):
    """A group input that the modifier exposes (and params()/set_params() read and write as `key`)."""
    key: str                  # snake_case name for params() / set_params()
    name: str                 # socket name, shown in Blender's modifier panel
    kind: str                 # a key of SOCKET_TYPES
    default: object = None
    min: float | None = None
    max: float | None = None
    description: str = ""
    options: tuple = ()       # MENU items
    subtype: str = ""         # e.g. DISTANCE, ANGLE, FACTOR, PERCENTAGE

    def schema(self) -> dict:
        """JSON Schema for the value set_params() takes."""
        kind = self.kind
        out = {"description": self.description}
        if kind in ("FLOAT", "INT"):
            out["type"] = "number" if kind == "FLOAT" else "integer"
            out["default"] = self.default
            if self.min is not None:
                out["minimum"] = self.min
            if self.max is not None:
                out["maximum"] = self.max
        elif kind == "BOOL":
            out.update(type="boolean", default=bool(self.default))
        elif kind == "VECTOR":
            out.update(type="array", items={"type": "number"}, minItems=3, maxItems=3, default=list(self.default))
        elif kind == "MENU":
            out.update(type="string", enum=[o.lower() for o in self.options], default=self.default.lower())
        elif kind == "STRING":
            out.update(type="string", default=self.default or "")
        elif kind in ("OBJECT", "COLLECTION"):
            out["type"] = ["string", "null"]
            out["description"] += f" ({kind.lower()} name, or null)"
        elif kind == "MATERIAL":
            out["type"] = ["string", "null"]
            out["description"] += " (a material preset such as 'stone', or a material name)"
            if self.default:
                out["default"] = self.default
        return out


def _single_value(sock) -> None:
    """A plain value in the modifier panel, never an attribute."""
    try:
        sock.structure_type = "SINGLE"
    except (AttributeError, TypeError):
        sock.force_non_field = True


def _available(sockets):
    return [s for s in sockets if getattr(s, "enabled", True) and not getattr(s, "is_unavailable", False)]


def socket(sockets, key):
    """The first available socket named `key`, or the key-th available one."""
    live = _available(sockets)
    if isinstance(key, int):
        return live[key]
    for s in live:
        if s.name == key or s.identifier == key:
            return s
    raise KeyError(f"no socket {key!r} among {[s.name for s in live]}")


def out(node, key=0):
    return socket(node.outputs, key)


class Graph:
    """Builds one GeometryNodeTree; nodes are laid out on a grid in the order they are made."""

    def __init__(self, tree):
        self.tree = tree
        self.nodes, self.links = tree.nodes, tree.links
        self.group_in = self.nodes.new("NodeGroupInput")
        self.group_out = self.nodes.new("NodeGroupOutput")
        self.group_in.location = (-400, 0)
        self._count = 0

    @classmethod
    def new(cls, name: str, inputs, outputs=(("Geometry", "GEOMETRY"),), geometry_input=False):
        """A new group with `inputs` (graph.Input) and `outputs`; geometry_input adds the modified
        object's own geometry as the first input (for modifiers that change it)."""
        tree = bpy.data.node_groups.new(name, "GeometryNodeTree")
        tree.is_modifier = True
        iface = tree.interface
        if geometry_input:
            iface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        for spec in inputs:
            if spec.kind == "MENU":
                continue  # added once the menu switch that feeds it exists (see menu())
            sock = iface.new_socket(spec.name, in_out="INPUT", socket_type=SOCKET_TYPES[spec.kind])
            sock.description = spec.description[:200]
            if spec.subtype and hasattr(sock, "subtype"):
                try:
                    sock.subtype = spec.subtype
                except TypeError:
                    pass
            if spec.default is not None and spec.kind not in ("OBJECT", "COLLECTION", "MATERIAL"):
                sock.default_value = spec.default
            if spec.min is not None and hasattr(sock, "min_value"):
                sock.min_value = spec.min
            if spec.max is not None and hasattr(sock, "max_value"):
                sock.max_value = spec.max
            if spec.kind in ("FLOAT", "INT", "BOOL", "VECTOR"):
                _single_value(sock)
        for name, kind in outputs:
            iface.new_socket(name, in_out="OUTPUT", socket_type=SOCKET_TYPES[kind])
        g = cls(tree)
        g.specs = list(inputs)
        return g

    def finish(self):
        """Put the inputs in spec order (menu inputs were added late) and return the tree."""
        iface = self.tree.interface
        for spec in self.specs:
            item = next((i for i in iface.items_tree if i.item_type == "SOCKET" and i.in_out == "INPUT"
                         and i.name == spec.name), None)
            if item is not None:
                iface.move(item, len(iface.items_tree))
        return self.tree

    # ------------------------------------------------------------------ nodes and links

    def node(self, kind: str, inputs=None, **props):
        n = self.nodes.new(kind)
        self._count += 1
        n.location = (200 * (self._count % 40), -200 * (self._count // 40))
        for key, value in props.items():
            setattr(n, key, value)
        if isinstance(inputs, dict):
            for key, value in inputs.items():
                self.set(socket(n.inputs, key), value)
        elif inputs:
            for i, value in enumerate(inputs):
                if value is not None:
                    self.set(socket(n.inputs, i), value)
        return n

    def set(self, target, value) -> None:
        if isinstance(value, bpy.types.NodeSocket):
            self.links.new(value, target)
        elif isinstance(value, bpy.types.Node):
            self.links.new(out(value), target)
        else:
            target.default_value = value

    def input(self, name: str):
        return socket(self.group_in.outputs, name)

    def output(self, geometry, name="Geometry") -> None:
        self.set(socket(self.group_out.inputs, name), geometry)

    def menu(self, spec: Input, data_type: str, values: dict):
        """The value of `values` ({item: value}, in spec.options order) that the MENU input `spec`
        picks. One Menu Switch per menu input turns the item into its index (a menu socket feeding
        several switches would see conflicting item lists); an Index Switch picks the value."""
        if not hasattr(self, "_menus"):
            self._menus = {}
        index = self._menus.get(spec.name)
        if index is None:
            sw = self.node("GeometryNodeMenuSwitch", data_type="INT")
            items = sw.enum_items
            while len(items) < len(spec.options):
                items.new("Item")
            for item, name in zip(items, spec.options):
                item.name = name
            sock = self.tree.interface.new_socket(spec.name, in_out="INPUT", socket_type="NodeSocketMenu")
            sock.description = spec.description[:200]
            self.links.new(self.input(spec.name), socket(sw.inputs, "Menu"))
            for i, name in enumerate(spec.options):
                self.set(socket(sw.inputs, name), i)
            sock.default_value = spec.default
            index = self._menus[spec.name] = out(sw)
        return self.index_switch(data_type, index, [values[name] for name in spec.options])

    # ------------------------------------------------------------------ helpers (return sockets)

    def math(self, op, a, b=None, c=None):
        return out(self.node("ShaderNodeMath", [a, b, c], operation=op))

    def add(self, a, b):
        return self.math("ADD", a, b)

    def sub(self, a, b):
        return self.math("SUBTRACT", a, b)

    def mul(self, a, b):
        return self.math("MULTIPLY", a, b)

    def div(self, a, b):
        return self.math("DIVIDE", a, b)

    def vec(self, op, a, b=None, c=None, scale=None):
        """Vector math; DOT_PRODUCT, LENGTH and DISTANCE return the Value output."""
        n = self.node("ShaderNodeVectorMath", operation=op)
        for i, value in enumerate((a, b, c)):
            if value is not None:
                self.set(socket(n.inputs, i), value)
        if scale is not None:
            self.set(socket(n.inputs, "Scale"), scale)
        return out(n, "Value" if op in ("DOT_PRODUCT", "LENGTH", "DISTANCE") else "Vector")

    def xyz(self, x=0.0, y=0.0, z=0.0):
        return out(self.node("ShaderNodeCombineXYZ", [x, y, z]))

    def sep(self, v):
        n = self.node("ShaderNodeSeparateXYZ", [v])
        return out(n, "X"), out(n, "Y"), out(n, "Z")

    def cmp(self, op, a, b, data_type="FLOAT"):
        return out(self.node("FunctionNodeCompare", [a, b], data_type=data_type, operation=op))

    def both(self, a, b):
        return out(self.node("FunctionNodeBooleanMath", [a, b], operation="AND"))

    def either(self, a, b):
        return out(self.node("FunctionNodeBooleanMath", [a, b], operation="OR"))

    def no(self, a):
        return out(self.node("FunctionNodeBooleanMath", [a], operation="NOT"))

    def switch(self, input_type, condition, false, true):
        return out(self.node("GeometryNodeSwitch", {"Switch": condition, "False": false, "True": true},
                             input_type=input_type))

    def random(self, data_type, lo, hi, seed, ident=None):
        inputs = {"Min": lo, "Max": hi, "Seed": seed}
        if data_type == "BOOLEAN":
            inputs = {"Probability": lo, "Seed": seed}
        if ident is not None:
            inputs["ID"] = ident
        return out(self.node("FunctionNodeRandomValue", inputs, data_type=data_type))

    def capture(self, geometry, value, domain="POINT", data_type="FLOAT"):
        """(geometry, captured field) from Capture Attribute."""
        n = self.node("GeometryNodeCaptureAttribute", domain=domain)
        n.capture_items.new({"FLOAT_VECTOR": "VECTOR"}.get(data_type, data_type), "Value")
        self.set(socket(n.inputs, "Geometry"), geometry)
        self.set(socket(n.inputs, "Value"), value)
        return out(n, "Geometry"), out(n, "Value")

    def sample_index(self, geometry, value, index, data_type="FLOAT", domain="POINT"):
        return out(self.node("GeometryNodeSampleIndex", {"Geometry": geometry, "Value": value, "Index": index},
                             data_type=data_type, domain=domain))

    def on_domain(self, value, domain, data_type="FLOAT"):
        return out(self.node("GeometryNodeFieldOnDomain", [value], domain=domain, data_type=data_type))

    def store(self, geometry, name, value, data_type="FLOAT", domain="POINT", selection=None):
        inputs = {"Geometry": geometry, "Name": name, "Value": value}
        if selection is not None:
            inputs["Selection"] = selection
        return out(self.node("GeometryNodeStoreNamedAttribute", inputs, data_type=data_type, domain=domain))

    def named(self, name, data_type="FLOAT"):
        n = self.node("GeometryNodeInputNamedAttribute", {"Name": name}, data_type=data_type)
        return out(n, "Attribute"), out(n, "Exists")

    def join(self, *geometries):
        n = self.node("GeometryNodeJoinGeometry")
        target = socket(n.inputs, "Geometry")
        for geo in geometries:
            if geo is not None:
                self.set(target, geo)
        return out(n)

    def index_switch(self, data_type, index, values):
        n = self.node("GeometryNodeIndexSwitch", data_type=data_type)
        while sum(1 for s in _available(n.inputs) if s.name.isdigit()) < len(values):
            n.index_switch_items.new()
        self.set(socket(n.inputs, "Index"), index)
        for i, value in enumerate(values):
            self.set(socket(n.inputs, str(i)), value)
        return out(n)

    def set_position(self, geometry, position=None, offset=None, selection=None):
        inputs = {"Geometry": geometry}
        if position is not None:
            inputs["Position"] = position
        if offset is not None:
            inputs["Offset"] = offset
        if selection is not None:
            inputs["Selection"] = selection
        return out(self.node("GeometryNodeSetPosition", inputs))

    def delete(self, geometry, selection, domain="POINT"):
        return out(self.node("GeometryNodeDeleteGeometry", {"Geometry": geometry, "Selection": selection},
                             domain=domain))

    def part(self, geometry, kind):
        """One component of a geometry: Mesh, Curve, Point Cloud or Instances."""
        return out(self.node("GeometryNodeSeparateComponents", {"Geometry": geometry}), kind)

    def position(self):
        return out(self.node("GeometryNodeInputPosition"))

    def index(self):
        return out(self.node("GeometryNodeInputIndex"))

    def object_geometry(self, obj_socket, as_instance=False):
        """An Object input's evaluated geometry in this modifier object's space."""
        n = self.node("GeometryNodeObjectInfo", {"Object": obj_socket, "As Instance": as_instance},
                      transform_space="RELATIVE")
        return out(n, "Geometry")

    def realize(self, geometry):
        return out(self.node("GeometryNodeRealizeInstances", {"Geometry": geometry}))

    def material(self, geometry, material, selection=None):
        inputs = {"Geometry": geometry, "Material": material}
        if selection is not None:
            inputs["Selection"] = selection
        return out(self.node("GeometryNodeSetMaterial", inputs))

    def flat(self, mesh):
        return out(self.node("GeometryNodeSetShadeSmooth", {"Mesh": mesh, "Shade Smooth": False}))

    def degrees(self, value):
        return self.mul(value, math.pi / 180.0)

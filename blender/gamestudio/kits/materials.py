# SPDX-License-Identifier: GPL-3.0-or-later
"""The shared material palette: a small set of stylized PBR presets used by every kit.

    presets() -> list[dict]                       name, description and values of every preset
    material(preset, **overrides) -> Material     the shared material (created on first use)
    apply(obj, preset, **overrides) -> Material   assign it to an object (all slots, or `slot=`)

Overrides: color, emission ("#RRGGBB" sRGB hex, or 3-4 linear floats as Blender stores them),
roughness, metallic, emission_strength, alpha (0-1), name (material name). apply() also takes
slot (index of the material slot to replace). ALIASES maps other common names (dark_wood, glow,
steel) to presets.

A preset's material is called GS_<Preset> (GS_Stone, GS_WoodDark) and is shared by every object
that uses it, so tweaking it in Blender restyles all of them. Overrides make a separate material,
GS_<Preset>_<hash> unless `name` is given, reused when the same overrides come again.
Base colours are stored linear, as Blender and glTF expect; the table below is in sRGB hex.
"""

import hashlib
import json
import math

import bpy

from .errors import KitError

PRESET_PROP = "gs_preset"      # the preset a material was made from
KEY_PROP = "gs_material_key"   # GS_<Preset>[_<hash>]: finds the shared material even if renamed

# name: (sRGB hex, roughness, metallic, emission strength, description)
_PRESETS = {
    "stone": ("#8F8A80", 0.90, 0.0, 0.0, "Warm grey rock: boulders, steps, walls"),
    "stone_dark": ("#5D5954", 0.90, 0.0, 0.0, "Dark stone: foundations, trims, cliffs"),
    "wood": ("#B27C48", 0.75, 0.0, 0.0, "Planks: crates, barrels, floors"),
    "wood_dark": ("#6E4A2E", 0.80, 0.0, 0.0, "Dark wood: frames, posts, beams"),
    "bark": ("#5A3F2B", 0.95, 0.0, 0.0, "Tree trunks and branches"),
    "leaves": ("#5E9E3C", 0.80, 0.0, 0.0, "Broadleaf foliage and bushes"),
    "pine": ("#2F6E46", 0.85, 0.0, 0.0, "Conifer needles"),
    "grass": ("#78B04A", 0.95, 0.0, 0.0, "Ground cover, platform tops"),
    "dirt": ("#8A6446", 1.00, 0.0, 0.0, "Soil, paths, terrain sides"),
    "sand": ("#D9C28E", 0.95, 0.0, 0.0, "Beaches, deserts"),
    "plaster": ("#E8DCC4", 0.90, 0.0, 0.0, "Painted walls"),
    "roof": ("#B5533C", 0.80, 0.0, 0.0, "Terracotta roof tiles"),
    "metal": ("#A9AFB5", 0.35, 1.0, 0.0, "Bright steel"),
    "iron": ("#4B4F55", 0.55, 1.0, 0.0, "Dark iron: bands, hinges, hoops"),
    "gold": ("#FFC23D", 0.25, 1.0, 0.0, "Shiny gold: coins, trophies"),
    "plastic": ("#E8E4DC", 0.45, 0.0, 0.0, "Neutral plastic; give it a color override"),
    "rubber": ("#2A2B30", 0.70, 0.0, 0.0, "Near-black matte: visors, tyres, details"),
    "skin": ("#F1C7A1", 0.60, 0.0, 0.0, "Character skin"),
    "gem": ("#23B5D8", 0.15, 0.0, 0.35, "Glossy crystal with a faint glow"),
    "emissive": ("#FFD45E", 0.50, 0.0, 4.0, "Glowing light source: lamps, pickups, lava (set color)"),
    "brick": ("#A2503A", 0.85, 0.0, 0.0, "Red brick walls"),
    "concrete": ("#A3A19B", 0.90, 0.0, 0.0, "Poured concrete: bunkers, modern floors"),
    "copper": ("#D9875F", 0.30, 1.0, 0.0, "Copper and bronze"),
    "snow": ("#F2F5F8", 0.60, 0.0, 0.0, "Snow caps and winter ground"),
    "ice": ("#B5DEF0", 0.10, 0.0, 0.0, "Slightly see-through ice"),
    "water": ("#3A86C2", 0.05, 0.0, 0.0, "See-through water surfaces"),
    "glass": ("#E6F2FA", 0.05, 0.0, 0.0, "Clear glass: windows, bottles"),
}
_ALPHA = {"ice": 0.85, "water": 0.7, "glass": 0.25}
# Other names agents use for the same looks.
ALIASES = {"dark_wood": "wood_dark", "glow": "emissive", "steel": "metal", "rock": "stone", "foliage": "leaves"}

OVERRIDES = ("color", "roughness", "metallic", "emission", "emission_strength", "alpha", "name")


def preset_names() -> list[str]:
    return list(_PRESETS)


def canonical(preset: str) -> str:
    """The preset name for an alias (dark_wood -> wood_dark); other names unchanged."""
    return ALIASES.get(preset, preset)


def presets() -> list[dict]:
    out = []
    for name, (hex_color, roughness, metallic, strength, description) in _PRESETS.items():
        out.append({"name": name, "description": description, "material": _material_name(name),
                    "color": hex_color, "roughness": roughness, "metallic": metallic,
                    "emission": hex_color if strength else None, "emission_strength": strength,
                    "alpha": _ALPHA.get(name, 1.0), "aliases": sorted(a for a, p in ALIASES.items() if p == name)})
    return out


def srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def hex_to_linear(value: str) -> tuple:
    value = value.lstrip("#")
    return tuple(srgb_to_linear(int(value[i:i + 2], 16) / 255.0) for i in (0, 2, 4))


def _color(value, what) -> tuple:
    """A linear RGB tuple from "#RRGGBB" (sRGB) or 3-4 floats (already linear)."""
    if isinstance(value, str):
        text = value.strip()
        if len(text) == 7 and text[0] == "#":
            try:
                return hex_to_linear(text)
            except ValueError:
                pass
        raise KitError(f"{what} must be '#RRGGBB' or [r, g, b] in 0-1 (linear), got {value!r}")
    try:
        parts = [float(c) for c in value]
    except (TypeError, ValueError):
        raise KitError(f"{what} must be '#RRGGBB' or [r, g, b] in 0-1 (linear), got {value!r}") from None
    if len(parts) not in (3, 4) or not all(math.isfinite(c) and c >= 0 for c in parts):
        raise KitError(f"{what} must be 3 or 4 non-negative numbers, got {value!r}")
    return tuple(parts[:3])


def _unit(value, what) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0:
        raise KitError(f"{what} must be a number from 0 to 1, got {value!r}")
    return float(value)


def _material_name(preset: str) -> str:
    return "GS_" + "".join(part.title() for part in preset.split("_"))


def resolve(preset: str, overrides: dict) -> dict:
    """The final values for a preset plus overrides. Raises KitError for bad input."""
    if not isinstance(preset, str):
        raise KitError(f"preset must be a preset name, got {preset!r}")
    preset = canonical(preset)
    if preset not in _PRESETS:
        raise KitError(f"Unknown material preset {preset!r}. Presets: {', '.join(_PRESETS)}")
    unknown = sorted(set(overrides) - set(OVERRIDES))
    if unknown:
        raise KitError(f"Unknown material override(s) {', '.join(unknown)}. Valid: {', '.join(OVERRIDES)}")
    hex_color, roughness, metallic, strength, _ = _PRESETS[preset]
    color = hex_to_linear(hex_color)
    values = {"color": color, "roughness": roughness, "metallic": metallic,
              "emission": color if strength else (0.0, 0.0, 0.0), "emission_strength": strength,
              "alpha": _ALPHA.get(preset, 1.0)}
    if "color" in overrides:
        values["color"] = _color(overrides["color"], "color")
        if strength and "emission" not in overrides:
            values["emission"] = values["color"]  # a recoloured glow glows in the new colour
    if "emission" in overrides:
        values["emission"] = _color(overrides["emission"], "emission")
        if "emission_strength" not in overrides and not values["emission_strength"]:
            values["emission_strength"] = 1.0
    for key in ("roughness", "metallic", "alpha"):
        if key in overrides:
            values[key] = _unit(overrides[key], key)
    if "emission_strength" in overrides:
        s = overrides["emission_strength"]
        if isinstance(s, bool) or not isinstance(s, (int, float)) or not 0.0 <= s <= 1000.0:
            raise KitError(f"emission_strength must be a number from 0 to 1000, got {s!r}")
        values["emission_strength"] = float(s)
    return values


def material(preset: str, **overrides) -> bpy.types.Material:
    """The material for a preset (with overrides), created on first use and then shared."""
    values = resolve(preset, overrides)
    preset = canonical(preset)
    name = overrides.get("name")
    if name is not None:
        if not isinstance(name, str) or not name.strip():
            raise KitError(f"name must be a non-empty string, got {name!r}")
        mat = bpy.data.materials.get(name)  # an explicit name: (re)define that material
        if mat is None or mat.library is not None:
            mat = bpy.data.materials.new(name)
    else:
        key = _material_name(preset)
        tweaks = {k: v for k, v in overrides.items() if k != "name"}
        if tweaks:
            text = json.dumps({k: (list(v) if isinstance(v, tuple) else v) for k, v in sorted(tweaks.items())})
            key += "_" + hashlib.sha1(text.encode()).hexdigest()[:6]
        for existing in bpy.data.materials:
            if existing.get(KEY_PROP) == key:
                return existing  # shared: keep any tweaks the user made to it in Blender
        mat = bpy.data.materials.new(key)
        mat[KEY_PROP] = key
    _setup(mat, values)
    mat[PRESET_PROP] = preset
    return mat


def _setup(mat, v) -> None:
    rgba = (*v["color"], v["alpha"])
    mat.diffuse_color = rgba            # Solid view and Workbench renders
    mat.roughness = v["roughness"]
    mat.metallic = v["metallic"]
    tree = mat.node_tree
    if tree is None and hasattr(mat, "use_nodes"):  # Blender < 5.0
        mat.use_nodes = True
        tree = mat.node_tree
    bsdf = next((n for n in tree.nodes if n.type == "BSDF_PRINCIPLED"), None) if tree else None
    if bsdf is None and tree is not None:
        bsdf = tree.nodes.new("ShaderNodeBsdfPrincipled")
        out = next((n for n in tree.nodes if n.type == "OUTPUT_MATERIAL"), None)
        if out is None:
            out = tree.nodes.new("ShaderNodeOutputMaterial")
        tree.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    if bsdf is not None:
        _set_input(bsdf, "Base Color", rgba)
        _set_input(bsdf, "Roughness", v["roughness"])
        _set_input(bsdf, "Metallic", v["metallic"])
        _set_input(bsdf, "Alpha", v["alpha"])
        _set_input(bsdf, ("Emission Color", "Emission"), (*v["emission"], 1.0))
        _set_input(bsdf, "Emission Strength", v["emission_strength"])
    if v["alpha"] < 1.0 and hasattr(mat, "surface_render_method"):
        mat.surface_render_method = "BLENDED"


def _set_input(node, names, value) -> None:
    for name in (names,) if isinstance(names, str) else names:
        socket = node.inputs.get(name)
        if socket is not None:
            socket.default_value = value
            return


def apply(obj, preset: str, **overrides) -> bpy.types.Material:
    """Give `obj` a preset material. Without `slot`, every slot gets it (an object with no slots gets
    one); with `slot=i`, only that slot changes. Returns the material."""
    slot = overrides.pop("slot", None)
    if obj is None or getattr(obj, "data", None) is None or not hasattr(obj.data, "materials"):
        raise KitError(f"{getattr(obj, 'name', obj)!r} cannot have materials (it is not a mesh, curve or text)")
    mat = material(preset, **overrides)
    slots = obj.material_slots
    if slot is not None:
        if isinstance(slot, bool) or not isinstance(slot, int) or not 0 <= slot < max(1, len(slots)):
            raise KitError(f"slot must be 0 to {max(0, len(slots) - 1)} for {obj.name}, got {slot!r}")
        if not slots:
            obj.data.materials.append(mat)
        else:
            slots[slot].material = mat
        return mat
    if not slots:
        obj.data.materials.append(mat)
    for s in slots:
        s.material = mat
    return mat

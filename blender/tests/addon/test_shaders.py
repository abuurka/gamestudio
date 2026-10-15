# SPDX-License-Identifier: GPL-3.0-or-later
"""Game shaders on materials (core/shaders.py, Material.gs, Material.gss_<Name>): registry generation and
migration, the Python API, the Game Shader selector and menu (the project's shaders, then the shader
library by category) and the Material properties Game panel."""

import copy
import types

import bpy

from gamestudio import ui
from gamestudio.core import registry, shaders

import helpers
from test_ui import ICONS, MockLayout, _FakeSelf

SHADERS = [
    {"name": "Triplanar", "description": "World-space photo scans with wetness", "builtin": False,
     "source": "src/shaders/triplanar.ts", "fields": [
         {"name": "diffuse", "type": "asset", "extensions": [".webp", ".png", ".jpg"]},
         {"name": "tile", "type": "float", "default": 3.2, "min": 0.01},
         {"name": "wet", "type": "float", "default": 0, "min": 0, "max": 1}]},
    {"name": "Foliage", "description": "", "builtin": False, "source": "src/shaders/foliage.ts", "fields": [
        {"name": "sway", "type": "float", "default": 0.5},
        {"name": "mode", "type": "enum", "options": ["wind", "still"], "default": "wind"},
        {"name": "tint", "type": "color", "default": [1, 1, 1, 1]},
        {"name": "count", "type": "int", "default": 2}]},
]


def _library(name, category, description="", fields=()) -> dict:
    entry = {"name": name, "description": description or f"Library {name}", "builtin": True, "fields": list(fields)}
    if category is not None:
        entry["category"] = category
    return entry


# Built-in shaders as the shader library lists them (names from the library contract), in the registry's
# name order, plus two that the menu lists under "Other": Rainbow (an unknown category), Swirl (none).
LIBRARY = [
    _library("Caustics", "Water"),
    _library("Dissolve", "Magic & Sci-fi", "Noise edge glow", [
        {"name": "progress", "type": "float", "default": 0, "min": 0, "max": 1},
        {"name": "edgeColor", "type": "color", "default": [1, 0.5, 0.1, 1]}]),
    _library("GrassWind", "Nature"),
    _library("HitFlash", "Gameplay"),
    _library("Hologram", "Magic & Sci-fi"),
    _library("Rainbow", "Weather"),
    _library("SnowCover", "Nature"),
    _library("Swirl", None),
    _library("Toon", "Style"),
    _library("Water", "Water", "Waves, foam and depth colour", [
        {"name": "speed", "type": "float", "default": 1, "min": 0}]),
]


def shader_registry(mutate=None, library=False) -> dict:
    """The fixture registry plus the game shaders above, with library: True the built-in ones too
    (mutate(shaders) changes them first)."""
    data = helpers.fixture_registry()
    data["shaders"] = copy.deepcopy(SHADERS + (LIBRARY if library else []))
    if mutate is not None:
        mutate(data["shaders"])
    return data


def shader_entry(shader_list, name) -> dict:
    return next(s for s in shader_list if s["name"] == name)


class ShaderTestCase(helpers.ProjectTestCase):
    registry_data = shader_registry()


# --- Registry -------------------------------------------------------------------------------------


class ShaderRegistryTest(ShaderTestCase):
    def test_shaders_get_material_property_groups(self):
        self.assertEqual(registry.shader_names(), ["Triplanar", "Foliage"])
        self.assertEqual(registry.component_names(), [c["name"] for c in helpers.fixture_registry()["components"]])
        for name in ("Triplanar", "Foliage"):
            self.assertEqual(registry.shader_class(name).__name__, f"GSS_{name}")
            self.assertIn(f"gss_{name}", bpy.types.Material.bl_rna.properties)
            self.assertNotIn(f"gss_{name}", bpy.types.Object.bl_rna.properties)
            self.assertNotIn(f"gsc_{name}", bpy.types.Object.bl_rna.properties)
        self.assertEqual(registry.get_shader_schema("Triplanar")["source"], "src/shaders/triplanar.ts")
        self.assertEqual(registry.get_shader_field("Triplanar", "tile")["default"], 3.2)
        self.assertIsNone(registry.get_shader_schema("Spin"))
        self.assertIsNone(registry.get_schema("Triplanar"))
        registry.shader_schemas()[0]["fields"].clear()  # copies
        self.assertEqual(len(registry.get_shader_schema("Triplanar")["fields"]), 3)
        props = registry.shader_class("Triplanar").bl_rna.properties
        self.assertEqual(props["tile"].default, 3.2000000476837158)
        self.assertEqual(props["tile"].hard_min, 0.009999999776482582)
        self.assertEqual(props["diffuse"].subtype, "FILE_PATH")
        self.assertTrue(props["wet"].is_overridable)
        self.assertTrue(bpy.types.Material.bl_rna.properties["gss_Triplanar"].is_overridable)
        self.assertIn("shader", bpy.types.Material.bl_rna.properties["gs"].fixed_type.properties)

    def test_a_registry_without_shaders_has_none(self):
        registry.set_registry(helpers.fixture_registry())  # an older dev server: no "shaders" key
        self.assertEqual(registry.shader_names(), [])
        self.assertEqual(registry.shader_schemas(), [])
        self.assertNotIn("gss_Triplanar", bpy.types.Material.bl_rna.properties)
        self.assertIn("gs", bpy.types.Material.bl_rna.properties)
        self.assertIn("Spin", registry.component_names())

    def test_bad_shader_entries_are_skipped_with_warnings(self):
        def mutate(found):
            found += [{"name": "lowercase", "fields": []}, {"name": "Triplanar", "fields": []}, "nope",
                      {"name": "Water", "fields": [{"name": "depth", "type": "float", "default": 2},
                                                   {"name": "follow", "type": "entity"},
                                                   {"name": "Bad Name", "type": "float"},
                                                   {"name": "depth", "type": "int"}]}]
        registry.set_registry(shader_registry(mutate))
        self.assertEqual(registry.shader_names(), ["Triplanar", "Foliage", "Water"])
        self.assertEqual([f["name"] for f in registry.get_shader_schema("Water")["fields"]], ["depth"])
        problems = "\n".join(registry.warnings())
        for text in ("invalid name 'lowercase'", "duplicate shader 'Triplanar'", "not an object",
                     "Water.follow: shaders cannot have entity fields", "Water.Bad Name", "Water.depth: duplicate"):
            self.assertIn(text, problems)
        data = helpers.fixture_registry()
        data["shaders"] = {"Triplanar": {}}
        registry.set_registry(data)
        self.assertEqual(registry.shader_names(), [])
        self.assertIn("'shaders' must be a list", registry.warnings()[0])

    def test_a_project_shader_replaces_the_library_one_of_its_name(self):
        builtin = _library("Triplanar", "Surfaces", "The library's triplanar", [{"name": "scale", "type": "float"}])
        for shader_list in (SHADERS + [builtin], [builtin] + SHADERS):  # either order
            registry.set_registry(dict(helpers.fixture_registry(), shaders=copy.deepcopy(shader_list)))
            self.assertEqual(sorted(registry.shader_names()), ["Foliage", "Triplanar"])
            entry = registry.get_shader_schema("Triplanar")
            self.assertEqual((entry["builtin"], entry["source"]), (False, "src/shaders/triplanar.ts"))
            self.assertEqual([f["name"] for f in entry["fields"]], ["diffuse", "tile", "wet"])
            self.assertEqual(registry.warnings(), [])
        registry.set_registry(dict(helpers.fixture_registry(), shaders=[builtin, dict(builtin, description="2")]))
        self.assertEqual(registry.get_shader_schema("Triplanar")["description"], "The library's triplanar")
        self.assertIn("duplicate shader 'Triplanar'", registry.warnings()[0])

    def test_categories_are_kept_when_they_are_strings(self):
        registry.set_registry(shader_registry(lambda found: found.extend(
            [_library("Water", "Water"), _library("Odd", 7), _library("Blank", " ")])))
        self.assertEqual(registry.get_shader_schema("Water")["category"], "Water")
        self.assertNotIn("category", registry.get_shader_schema("Odd"))
        self.assertNotIn("category", registry.get_shader_schema("Blank"))

    def test_unregister_removes_the_groups(self):
        registry._apply([], [])
        self.assertNotIn("gss_Triplanar", bpy.types.Material.bl_rna.properties)
        self.assertIsNone(registry.shader_class("Triplanar"))
        registry.reload_registry(self.root)
        self.assertIn("gss_Triplanar", bpy.types.Material.bl_rna.properties)


class ShaderRegenerationTest(ShaderTestCase):
    def setUp(self):
        super().setUp()
        self.mat = bpy.data.materials.new("Sand")
        shaders.set_shader(self.mat, "Foliage", {"sway": 2.5, "mode": "still", "count": 5})
        shaders.set_shader(self.mat, "Triplanar", {"tile": 4, "wet": 0.6, "diffuse": "assets/sand.webp"})

    def values_of(self, name) -> dict:
        shaders.set_shader(self.mat, name)
        return shaders.get_values(self.mat)

    def test_values_survive_added_renamed_removed_and_retyped_fields(self):
        def mutate(found):
            tri = shader_entry(found, "Triplanar")
            tri["fields"] = [f for f in tri["fields"] if f["name"] != "diffuse"]           # removed
            tri["fields"].append({"name": "gloss", "type": "bool", "default": True})        # added
            next(f for f in tri["fields"] if f["name"] == "wet")["name"] = "wetness"        # renamed
            fol = shader_entry(found, "Foliage")
            for f in fol["fields"]:
                if f["name"] == "count":
                    f.update(type="float", default=2.0)                                     # int -> float
                elif f["name"] == "sway":
                    f.update(type="int", default=1)                                         # float -> int
                elif f["name"] == "mode":
                    f["options"] = ["gust", "still", "wind"]                                # reordered, added

        registry.set_registry(shader_registry(mutate))
        self.assertEqual(shaders.get_values(self.mat), {"tile": 4.0, "wetness": 0.0, "gloss": True})
        # Foliage is not the material's shader now, but its stored values were migrated too.
        self.assertEqual(self.values_of("Foliage"), {"sway": 2, "mode": "still", "tint": [1.0, 1.0, 1.0, 1.0],
                                                     "count": 5.0})
        registry.set_registry(shader_registry())  # back: the renamed field's value was kept under its name
        self.assertEqual(self.values_of("Triplanar"), {"diffuse": "assets/sand.webp", "tile": 4.0, "wet": 0.6})

    def test_removed_enum_option_falls_back_to_default(self):
        registry.set_registry(shader_registry(lambda found: next(
            f for f in shader_entry(found, "Foliage")["fields"] if f["name"] == "mode").update(options=["wind"])))
        self.assertEqual(self.values_of("Foliage")["mode"], "wind")
        self.assertEqual(shaders.get_values(self.mat)["count"], 5)

    def test_shader_removed_from_registry_keeps_its_name_and_values(self):
        registry.set_registry(shader_registry(lambda found: found.remove(shader_entry(found, "Triplanar"))))
        self.assertNotIn("gss_Triplanar", bpy.types.Material.bl_rna.properties)
        self.assertEqual(shaders.get_shader(self.mat), "Triplanar")
        self.assertTrue(shaders.is_missing(self.mat))
        self.assertEqual(shaders.get_values(self.mat), {})
        with self.assertRaises(ValueError) as cm:
            shaders.set_values(self.mat, {"tile": 1})
        self.assertIn("Unknown game shader 'Triplanar'", str(cm.exception))
        registry.set_registry(shader_registry())  # the definition returns
        self.assertFalse(shaders.is_missing(self.mat))
        self.assertEqual(shaders.get_values(self.mat), {"diffuse": "assets/sand.webp", "tile": 4.0, "wet": 0.6})

    def test_unchanged_shaders_are_not_re_registered(self):
        before = registry.shader_class("Triplanar")
        components_before = registry.component_class("Spin")
        registry.set_registry(shader_registry(lambda found: shader_entry(found, "Foliage")["fields"].append(
            {"name": "extra", "type": "int"})))
        self.assertIs(registry.shader_class("Triplanar"), before)
        self.assertIs(registry.component_class("Spin"), components_before)
        self.assertIn("extra", registry.shader_class("Foliage").bl_rna.properties)


# --- Python API -----------------------------------------------------------------------------------


class ShaderApiTest(ShaderTestCase):
    def setUp(self):
        super().setUp()
        self.mat = bpy.data.materials.new("Sand")

    def test_set_get_clear(self):
        self.assertIsNone(shaders.get_shader(self.mat))
        self.assertEqual(shaders.get_values(self.mat), {})
        self.assertEqual(shaders.set_shader(self.mat, "Triplanar"), [])
        self.assertEqual(shaders.get_shader(self.mat), "Triplanar")
        self.assertEqual(self.mat.gs.shader, "Triplanar")
        self.assertEqual(shaders.get_values(self.mat), {"diffuse": "", "tile": 3.2, "wet": 0.0})
        shaders.set_values(self.mat, {"wet": 0.25, "tile": "6"})
        self.assertEqual(shaders.get_values(self.mat), {"diffuse": "", "tile": 6.0, "wet": 0.25})
        self.assertEqual(shaders.changed_values(self.mat), {"tile": 6.0, "wet": 0.25})
        self.assertEqual(shaders.set_shader(self.mat, None), [])
        self.assertIsNone(shaders.get_shader(self.mat))
        self.assertEqual(self.mat.gs.shader, "")
        self.assertEqual(shaders.get_values(self.mat), {})

    def test_values_stay_stored_when_switching(self):
        shaders.set_shader(self.mat, "Triplanar", {"tile": 5})
        shaders.set_shader(self.mat, "Foliage", {"mode": "still"})
        self.assertEqual(shaders.get_values(self.mat)["mode"], "still")
        shaders.set_shader(self.mat, "")
        shaders.set_shader(self.mat, "Triplanar")
        self.assertEqual(shaders.get_values(self.mat)["tile"], 5.0)
        shaders.set_shader(self.mat, "Foliage")
        self.assertEqual(shaders.get_values(self.mat)["mode"], "still")

    def test_validation_errors_change_nothing(self):
        shaders.set_shader(self.mat, "Triplanar", {"wet": 0.5})
        cases = (
            (("Triplanr",), "Unknown game shader 'Triplanr'. Known: Triplanar, Foliage. Did you mean 'Triplanar'?"),
            (("Foliage", {"sway": "lots"}), "Field 'sway' expects a number"),
            (("Foliage", {"swing": 1}), "Game shader 'Foliage' has no field 'swing'. Fields: sway, mode, tint, count."),
            (("Triplanar", {"wet": 2}), "between 0 and 1"),
            (("Triplanar", {"diffuse": "a.tga"}), "expects a file ending in .webp, .png, .jpg"),
            (("Triplanar", ["wet"]), "must be a dict"),
            ((None, {"wet": 1}), "Values need a game shader"),
            (("bad name",), "Game shader names look like"),
        )
        for args, message in cases:
            with self.assertRaises(ValueError, msg=args) as cm:
                shaders.set_shader(self.mat, *args)
            self.assertIn(message, str(cm.exception))
            with self.assertRaises(ValueError):
                shaders.validate_shader(self.mat, *args)
        self.assertEqual(shaders.get_shader(self.mat), "Triplanar")
        self.assertEqual(shaders.get_values(self.mat), {"diffuse": "", "tile": 3.2, "wet": 0.5})
        with self.assertRaises(ValueError):
            shaders.set_values(self.mat, {"wet": 0.1, "tile": -1})
        self.assertEqual(shaders.get_values(self.mat)["wet"], 0.5)
        plain = bpy.data.materials.new("Plain")
        with self.assertRaises(ValueError) as cm:
            shaders.set_values(plain, {"wet": 0.1})
        self.assertIn("has no game shader", str(cm.exception))
        with self.assertRaises(ValueError):
            shaders.get_shader(helpers.new_object("NotAMaterial"))

    def test_empty_registry_stores_the_name_with_a_warning(self):
        registry.set_registry(helpers.fixture_registry())
        found = shaders.set_shader(self.mat, "Triplanar")
        self.assertEqual(len(found), 1)
        self.assertIn("no game shaders yet", found[0])
        self.assertEqual(shaders.validate_shader(self.mat, "Water"), [found[0].replace("Triplanar", "Water")])
        self.assertEqual(shaders.get_shader(self.mat), "Triplanar")
        self.assertEqual(shaders.get_values(self.mat), {})
        with self.assertRaises(ValueError) as cm:
            shaders.set_shader(self.mat, "Foliage", {"sway": 1})
        self.assertIn("Cannot check values", str(cm.exception))
        self.assertEqual(shaders.get_shader(self.mat), "Triplanar")
        registry.set_registry(shader_registry())
        self.assertEqual(shaders.get_values(self.mat)["tile"], 3.2)

    def test_linked_materials_are_read_only(self):
        lib_mat = bpy.data.materials.new("LibStone")
        shaders.set_shader(lib_mat, "Triplanar", {"wet": 0.75})
        lib_path = str(self.root / "lib.blend")
        bpy.data.libraries.write(lib_path, {lib_mat})
        helpers.reset_scene()
        with bpy.data.libraries.load(lib_path, link=True) as (_src, dst):
            dst.materials = ["LibStone"]
        linked = dst.materials[0]
        self.assertIsNotNone(linked.library)
        self.assertFalse(shaders.is_writable(linked))
        self.assertEqual(shaders.get_shader(linked), "Triplanar")  # reading works
        self.assertEqual(shaders.get_values(linked)["wet"], 0.75)
        for call in (lambda: shaders.set_shader(linked, "Foliage"), lambda: shaders.set_shader(linked, None),
                     lambda: shaders.set_values(linked, {"wet": 0.1})):
            with self.assertRaises(shaders.ReadOnlyMaterialError) as cm:
                call()
            self.assertIn("linked from", str(cm.exception))
            self.assertIsInstance(cm.exception, ValueError)
        self.assertEqual(shaders.get_shader(linked), "Triplanar")
        self.assertEqual(shaders.get_values(linked)["wet"], 0.75)

    def test_export_values_give_project_relative_asset_paths(self):
        path = (self.root / "assets" / "scans" / "sand_diff.webp").as_posix()
        shaders.set_shader(self.mat, "Triplanar", {"diffuse": path, "tile": 3.2, "wet": 0.6})
        self.assertEqual(shaders.get_values(self.mat)["diffuse"], path)
        self.assertEqual(shaders.get_values(self.mat, for_export=True),
                         {"diffuse": "assets/scans/sand_diff.webp", "tile": 3.2, "wet": 0.6})
        self.assertEqual(shaders.changed_values(self.mat), {"diffuse": path, "wet": 0.6})

    def test_shader_materials_of_a_scene(self):
        scene = bpy.context.scene
        bpy.data.materials.remove(self.mat)
        sand, rock, leaf, plain, prefab_mat = (bpy.data.materials.new(n)
                                               for n in ("Sand", "Rock", "Leaf", "Plain", "Gold"))
        for mat, name in ((sand, "Triplanar"), (rock, "Triplanar"), (leaf, "Foliage"), (prefab_mat, "Foliage")):
            shaders.set_shader(mat, name)
        ground = helpers.new_object("Ground", helpers.new_mesh("G"))
        ground.data.materials.append(plain)
        ground.data.materials.append(sand)
        wall = helpers.new_object("Wall", helpers.new_mesh("W"))
        wall.data.materials.append(sand)  # shared: listed once
        # A collection instance shows a collection kept outside the scene.
        trees = bpy.data.collections.new("Trees")
        tree = helpers.new_object("Tree", helpers.new_mesh("T"), collection=trees)
        tree.data.materials.append(plain)
        tree.material_slots[0].link = "OBJECT"
        tree.material_slots[0].material = leaf  # what Blender draws
        inst = helpers.new_object("Trees.001")
        inst.instance_type = "COLLECTION"
        inst.instance_collection = trees
        # A prefab no one places is still exported (spawnable).
        prefab = bpy.data.collections.new("Coin")
        prefab["gs_prefab"] = True
        helpers.new_object("CoinMesh", helpers.new_mesh("C"), collection=prefab).data.materials.append(prefab_mat)
        # Not in the scene: its material is not listed.
        helpers.new_object("Elsewhere", helpers.new_mesh("E"), collection=bpy.data.collections.new("Off")) \
            .data.materials.append(rock)
        self.assertEqual([m.name for m in shaders.shader_materials(scene)], ["Sand", "Leaf", "Gold"])
        self.assertEqual(shaders.users(sand), ["Ground", "Wall"])


# --- The Game Shader selector and the Material properties Game panel ---------------------------------------


class MaterialPanelTest(ShaderTestCase):
    def setUp(self):
        super().setUp()
        self.mat = bpy.data.materials.new("Sand")

    def draw(self, mat) -> str:
        texts = []
        context = types.SimpleNamespace(material=mat, scene=bpy.context.scene)
        cls = ui.MATERIAL_PT_gamestudio_shader
        self.assertTrue(cls.poll(context))
        cls.draw(_FakeSelf(cls, MockLayout(self, texts)), context)
        return " ".join(texts)

    def test_panel_is_registered_in_material_properties(self):
        cls = ui.MATERIAL_PT_gamestudio_shader
        self.assertTrue(hasattr(bpy.types, "MATERIAL_PT_gamestudio_shader"))
        self.assertEqual((cls.bl_space_type, cls.bl_context, cls.bl_label), ("PROPERTIES", "material", "Game"))
        self.assertFalse(cls.poll(types.SimpleNamespace(material=None)))

    def test_selector_lists_none_the_registry_and_an_unknown_stored_name(self):
        items = registry._shader_menu_items(self.mat.gs, None)
        self.assertEqual([i[0] for i in items], ["__none__", "Foliage", "Triplanar"])  # A-Z
        self.assertEqual([i[3] for i in items], ["BLANK1", "FILE_SCRIPT", "FILE_SCRIPT"])
        self.assertEqual(items[2][2], "World-space photo scans with wetness")  # the tooltip
        self.assertEqual(self.mat.gs.shader_menu, "__none__")
        self.mat.gs.shader_menu = "Foliage"
        self.assertEqual(shaders.get_shader(self.mat), "Foliage")
        self.assertEqual(self.mat.gs.shader_menu, "Foliage")
        self.mat.gs.shader_menu = "__none__"
        self.assertIsNone(shaders.get_shader(self.mat))
        self.mat.gs.shader = "Gone"  # a shader deleted from code
        items = registry._shader_menu_items(self.mat.gs, None)
        self.assertEqual((items[-1][0], items[-1][3]), ("Gone", "ERROR"))
        self.assertEqual(self.mat.gs.shader_menu, "Gone")
        self.mat.gs.shader_menu = "Triplanar"
        self.assertEqual(shaders.get_shader(self.mat), "Triplanar")
        self.assertNotIn("Gone", [i[0] for i in registry._shader_menu_items(self.mat.gs, None)])

    def test_draws_in_every_state(self):
        text = self.draw(self.mat)
        self.assertNotIn("draws this material", text)
        self.assertNotIn("No game shaders", text)

        shaders.set_shader(self.mat, "Triplanar", {"wet": 0.4})
        text = self.draw(self.mat)
        self.assertIn("In the game, Triplanar draws this material. The viewport shows its Blender nodes.", text)
        self.assertIn("src/shaders/triplanar.ts", text)
        self.assertIn("World-space photo scans with wetness", text)

        self.mat.gs.shader = "Gone"
        text = self.draw(self.mat)
        self.assertIn("'Gone' is not in the registry", text)
        self.assertNotIn("draws this material", text)

        registry.set_registry(helpers.fixture_registry())
        text = self.draw(self.mat)
        self.assertIn("No game shaders in the registry", text)
        self.assertIn("'Gone' is not in the registry", text)

    def test_linked_material_draws_read_only(self):
        lib_mat = bpy.data.materials.new("LibStone")
        shaders.set_shader(lib_mat, "Foliage", {"mode": "still"})
        lib_path = str(self.root / "lib.blend")
        bpy.data.libraries.write(lib_path, {lib_mat})
        helpers.reset_scene()
        with bpy.data.libraries.load(lib_path, link=True) as (_src, dst):
            dst.materials = ["LibStone"]
        text = self.draw(dst.materials[0])
        self.assertIn("read-only", text)
        self.assertIn("In the game, Foliage draws this material.", text)


class RecordingLayout(MockLayout):
    """A MockLayout that also records menu buttons with their icons, as "<menu idname icon>"."""

    def _child(self, *_args, **_kwargs):
        return RecordingLayout(self._test, self.texts)

    row = column = box = split = grid_flow = column_flow = _child

    def menu(self, menu, text="", icon="NONE", **kwargs):
        super().menu(menu, text=text, icon=icon, **kwargs)
        self.texts.append(f"<menu {menu} {icon}>")


class ShaderLibraryMenuTest(helpers.ProjectTestCase):
    """The Game Shader menu with the shader library: the project's shaders, then the library by category."""

    registry_data = shader_registry(library=True)

    def setUp(self):
        super().setUp()
        self.mat = bpy.data.materials.new("Sand")

    def draw(self, draw, *args, operator_context=None) -> list:
        texts = []
        layout = RecordingLayout(self, texts)
        if operator_context is not None:
            layout.operator_context = operator_context
        draw(layout, *args)
        return texts

    def draw_menu(self, cls, mat, **kwargs) -> list:
        context = types.SimpleNamespace(material=mat)
        return self.draw(lambda layout: cls.draw(_FakeSelf(cls, layout), context), **kwargs)

    def test_groups_put_the_project_first_then_the_library_by_category(self):
        project_names, library = registry.shader_groups()
        self.assertEqual(project_names, ["Foliage", "Triplanar"])
        self.assertEqual(library, [
            ("Water", ["Caustics", "Water"]),
            ("Nature", ["GrassWind", "SnowCover"]),
            ("Magic & Sci-fi", ["Dissolve", "Hologram"]),
            ("Gameplay", ["HitFlash"]),
            ("Style", ["Toon"]),
            ("Other", ["Rainbow", "Swirl"]),  # an unknown category, and none
        ])
        self.assertEqual(registry.shader_icon("Water"), "MOD_OCEAN")
        self.assertEqual(registry.shader_icon("Triplanar"), "FILE_SCRIPT")
        self.assertEqual(registry.shader_icon("Swirl"), "SHADING_RENDERED")
        self.assertEqual(registry.shader_icon("Gone"), "ERROR")
        for icon in registry.SHADER_CATEGORY_ICONS.values():
            self.assertIn(icon, ICONS)

    def test_enum_items_follow_the_groups_with_descriptions_as_tooltips(self):
        items = registry._shader_menu_items(self.mat.gs, None)
        self.assertEqual([i[0] for i in items], [
            "__none__", "Foliage", "Triplanar", "Caustics", "Water", "GrassWind", "SnowCover", "Dissolve",
            "Hologram", "HitFlash", "Toon", "Rainbow", "Swirl"])
        by_name = {i[0]: i for i in items}
        self.assertEqual(by_name["Water"][2:4], ("Waves, foam and depth colour", "MOD_OCEAN"))
        self.assertEqual(by_name["Dissolve"][3], "SHADERFX")
        self.assertEqual(by_name["Foliage"][2], "Game shader Foliage")  # no description
        self.assertEqual([i[4] for i in items], list(range(len(items))))
        for item in items:
            self.assertIn(item[3], ICONS)
        self.mat.gs.shader_menu = "Water"
        self.assertEqual(shaders.get_values(self.mat), {"speed": 1.0})

    def test_menu_lists_none_the_project_then_a_sub_menu_per_category(self):
        texts = self.draw_menu(ui.GAMESTUDIO_MT_material_shader, self.mat, operator_context="EXEC_REGION_WIN")
        self.assertEqual(texts, [
            "Search...", "None", "Project", "Foliage", "Triplanar", "Library", "<context material=Sand>",
            "Water", "<menu GAMESTUDIO_MT_shader_library_water MOD_OCEAN>",
            "Nature", "<menu GAMESTUDIO_MT_shader_library_nature FORCE_WIND>",
            "Magic & Sci-fi", "<menu GAMESTUDIO_MT_shader_library_magic_sci_fi SHADERFX>",
            "Gameplay", "<menu GAMESTUDIO_MT_shader_library_gameplay CON_ACTION>",
            "Style", "<menu GAMESTUDIO_MT_shader_library_style BRUSHES_ALL>",
            "Other", "<menu GAMESTUDIO_MT_shader_library_other SHADING_RENDERED>"])
        self.assertEqual(ui.GAMESTUDIO_MT_material_shader.bl_options, {"SEARCH_ON_KEY_PRESS"})
        # Drawn for a menu search (another operator context), it has no Search... item of its own.
        self.assertNotIn("Search...", self.draw_menu(ui.GAMESTUDIO_MT_material_shader, self.mat))
        menus = {cls.bl_label: cls for cls in ui.SHADER_CATEGORY_MENUS}
        self.assertEqual(list(menus), list(registry.SHADER_CATEGORIES) + ["Other"])
        for cls in ui.SHADER_CATEGORY_MENUS:
            self.assertTrue(hasattr(bpy.types, cls.bl_idname), cls.bl_idname)
        self.assertEqual(self.draw_menu(menus["Water"], self.mat), ["Caustics", "Water"])
        self.assertEqual(self.draw_menu(menus["Other"], self.mat), ["Rainbow", "Swirl"])
        self.assertEqual(self.draw_menu(menus["Surfaces"], self.mat), ["No shaders"])  # none in this registry
        self.assertEqual(self.draw_menu(menus["Water"], None), ["No material"])
        self.assertEqual(self.draw_menu(ui.GAMESTUDIO_MT_material_shader, None), ["No material"])

    def test_an_unknown_stored_shader_shows_with_the_error_icon(self):
        self.mat.gs.shader = "Gone"
        texts = self.draw_menu(ui.GAMESTUDIO_MT_material_shader, self.mat)
        self.assertEqual(texts[:3], ["None", "Gone", "Project"])
        self.assertEqual(registry._shader_menu_items(self.mat.gs, None)[-1][3], "ERROR")
        texts = self.draw(ui.draw_material_shader, self.mat)
        self.assertIn(f"<menu {ui.SHADER_MENU} ERROR>", texts)
        self.assertIn("'Gone' is not in the registry: no src/shaders file or library shader defines it.",
                      " ".join(texts))

    def test_menu_without_shaders(self):
        registry.set_registry(helpers.fixture_registry())
        texts = self.draw_menu(ui.GAMESTUDIO_MT_material_shader, self.mat)
        self.assertEqual(texts, ["None", "No game shaders in the registry"])

    def test_panel_says_where_the_shader_comes_from(self):
        texts = self.draw(ui.draw_material_shader, self.mat)
        self.assertEqual(texts[:2], ["Game Shader", "<context material=Sand>"])
        self.assertIn(f"<menu {ui.SHADER_MENU} NONE>", texts)
        self.assertNotIn("Copy it into src/shaders to customise.", texts)

        shaders.set_shader(self.mat, "Triplanar")
        texts = self.draw(ui.draw_material_shader, self.mat)
        self.assertIn(f"<menu {ui.SHADER_MENU} FILE_SCRIPT>", texts)
        self.assertIn("From the project: src/shaders/triplanar.ts", texts)
        self.assertNotIn("Copy it into src/shaders to customise.", texts)

        shaders.set_shader(self.mat, "Dissolve", {"progress": 0.3})
        texts = self.draw(ui.draw_material_shader, self.mat)
        self.assertIn(f"<menu {ui.SHADER_MENU} SHADERFX>", texts)
        self.assertIn("Noise edge glow", texts)
        self.assertIn("From the shader library (Magic & Sci-fi)", texts)
        self.assertIn("Copy it into src/shaders to customise.", texts)
        self.assertIn("In the game, Dissolve draws this material. The viewport shows its Blender nodes.",
                      " ".join(texts))
        # The line comes after the description (and the fields), the hint after the line.
        self.assertLess(texts.index("Noise edge glow"), texts.index("From the shader library (Magic & Sci-fi)"))
        self.assertLess(texts.index("From the shader library (Magic & Sci-fi)"),
                        texts.index("Copy it into src/shaders to customise."))

        shaders.set_shader(self.mat, "Swirl")
        self.assertIn("From the shader library (Other)", self.draw(ui.draw_material_shader, self.mat))

    def test_library_source_files_open_when_they_exist(self):
        source = "node_modules/@gamestudio/engine/src/shaders/library/water/meta.ts"

        def with_source(found):
            shader_entry(found, "Water")["source"] = source
            shader_entry(found, "Foliage").pop("source")

        registry.set_registry(shader_registry(with_source, library=True))
        shaders.set_shader(self.mat, "Water")
        self.assertIn("From the shader library (Water)", self.draw(ui.draw_material_shader, self.mat))
        path = self.root / source
        path.parent.mkdir(parents=True)
        path.write_text("export {};\n", encoding="utf-8")
        self.assertIn(f"From the shader library (Water): {source}", self.draw(ui.draw_material_shader, self.mat))
        shaders.set_shader(self.mat, "Foliage")
        self.assertIn("From the project", self.draw(ui.draw_material_shader, self.mat))

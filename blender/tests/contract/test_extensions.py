# SPDX-License-Identifier: GPL-3.0-or-later
"""System extensions: how the gamestudio extension ships and loads.

GameStudio ships its add-on as a *system extension*: blender/gamestudio,
found through BLENDER_SYSTEM_EXTENSIONS (development, `gsfork run`/`test`) or the install's
`extensions/system` folder (packaged builds), and loaded as `bl_ext.system.gamestudio`.
"""

import shutil
import unittest

import contract_support as cs

EXT_ID = "gs_contract_probe_ext"
MANIFEST = f'''schema_version = "1.0.0"
id = "{EXT_ID}"
version = "1.0.0"
name = "GameStudio Contract Probe"
tagline = "Throw-away extension used by the GameStudio contract tests"
maintainer = "GameStudio contributors"
type = "add-on"
blender_version_min = "4.2.0"
license = ["SPDX:GPL-3.0-or-later"]
'''
INIT = '''
REGISTERED = False


def register():
    global REGISTERED
    REGISTERED = True


def unregister():
    global REGISTERED
    REGISTERED = False
'''


class SystemExtensionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = cs.make_tmp_dir("gs_contract_ext_")
        ext = cls.root / "system" / EXT_ID
        ext.mkdir(parents=True)
        (ext / "blender_manifest.toml").write_text(MANIFEST, encoding="utf-8")
        (ext / "__init__.py").write_text(INIT, encoding="utf-8")
        cls.report = cs.run_probe("probe_enable_extension", f"bl_ext.system.{EXT_ID}",
                                  env={"BLENDER_SYSTEM_EXTENSIONS": cls.root})

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_system_extension_loads_as_bl_ext_system(self):
        """`BLENDER_SYSTEM_EXTENSIONS/<parent>/system/<ext>` loads and registers as `bl_ext.system.<ext>`.

        Breaks if changed: build_template.py enables `bl_ext.system.gamestudio` in the template's
        userpref.blend, the shell tests look for that module, and the template's gs() lookup
        finds the extension by the `.gamestudio` suffix. A different repo layout or module prefix
        leaves the IDE without its game features.
        """
        report = self.report
        self.assertEqual(report["result"], ["FINISHED"], report)
        self.assertTrue(report["enabled"], report)
        self.assertTrue(report["in_sys_modules"], report)
        self.assertTrue(report["registered"], "the extension's register() did not run")
        self.assertEqual(report["package"], f"bl_ext.system.{EXT_ID}")

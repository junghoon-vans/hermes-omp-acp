"""Opt-in checks for metadata and entry points in built distributions."""

from email.parser import BytesParser
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile


DIST_DIR_ENV = "HERMES_OMP_DIST_DIR"
ENTRYPOINT_ENV = "HERMES_OMP_CHECK_INSTALLED_ENTRYPOINT"


@unittest.skipUnless(os.environ.get(DIST_DIR_ENV), f"set {DIST_DIR_ENV} to check built distributions")
class PackagingArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        from utils import fast_safe_load

        manifest = fast_safe_load((root / "plugin.yaml").read_text(encoding="utf-8"))
        cls.version = manifest["version"]
        cls.description = manifest["description"]

        dist_dir = Path(os.environ[DIST_DIR_ENV])
        wheels = sorted(dist_dir.glob("hermes_omp_acp-*.whl"))
        sdists = sorted(dist_dir.glob("hermes_omp_acp-*.tar.gz"))
        if len(wheels) != 1:
            raise AssertionError(f"expected one hermes_omp_acp wheel in {dist_dir}, found {len(wheels)}")
        if len(sdists) != 1:
            raise AssertionError(f"expected one hermes_omp_acp sdist in {dist_dir}, found {len(sdists)}")
        cls.wheel = wheels[0]
        cls.sdist = sdists[0]
        cls.root = root

    def assert_metadata_matches_manifest(self, metadata, artifact):
        self.assertEqual(metadata["Version"], self.version, f"{artifact} version")
        self.assertEqual(metadata["Summary"], self.description, f"{artifact} description")

    def test_built_wheel_metadata_matches_plugin_manifest(self):
        with zipfile.ZipFile(self.wheel) as wheel:
            metadata_files = [
                name for name in wheel.namelist() if name.endswith(".dist-info/METADATA")
            ]
            self.assertEqual(len(metadata_files), 1, "wheel must contain one dist-info/METADATA")
            metadata = BytesParser().parsebytes(wheel.read(metadata_files[0]))

        self.assert_metadata_matches_manifest(metadata, self.wheel.name)

    def test_built_sdist_metadata_matches_plugin_manifest(self):
        with tarfile.open(self.sdist, "r:gz") as archive:
            metadata_files = [
                member
                for member in archive.getmembers()
                if member.isfile() and Path(member.name).name == "PKG-INFO"
            ]
            self.assertEqual(len(metadata_files), 1, "sdist must contain one PKG-INFO")
            metadata_file = archive.extractfile(metadata_files[0])
            self.assertIsNotNone(metadata_file)
            metadata = BytesParser().parsebytes(metadata_file.read())

        self.assert_metadata_matches_manifest(metadata, self.sdist.name)

    @unittest.skipUnless(
        os.environ.get(ENTRYPOINT_ENV) == "1",
        f"set {ENTRYPOINT_ENV}=1 in the installed Hermes runtime to check the wheel entry point",
    )
    def test_installed_wheel_imports_and_loads_plugin_entrypoint(self):
        script = r"""
import importlib.metadata
from pathlib import Path
import sys

expected_version, expected_description, source_root = sys.argv[1:4]
import hermes_omp_acp

distribution = importlib.metadata.distribution("hermes-omp-acp")
if distribution.version != expected_version:
    raise AssertionError(f"installed version {distribution.version!r} != {expected_version!r}")
if distribution.metadata["Summary"] != expected_description:
    raise AssertionError("installed description does not match plugin.yaml")

package_dir = Path(distribution.locate_file("hermes_omp_acp")).resolve()
module_path = Path(hermes_omp_acp.__file__).resolve()
if not module_path.is_relative_to(package_dir):
    raise AssertionError(f"package imported outside the installed distribution: {module_path}")
if module_path.is_relative_to(Path(source_root).resolve() / "hermes_omp_acp"):
    raise AssertionError(f"package imported from the source checkout: {module_path}")

entrypoints = [
    entrypoint
    for entrypoint in distribution.entry_points
    if entrypoint.group == "hermes_agent.plugins" and entrypoint.name == "omp-acp-provider"
]
if len(entrypoints) != 1:
    raise AssertionError(f"expected one plugin entry point, found {len(entrypoints)}")
if entrypoints[0].value != "hermes_omp_acp.provider:register":
    raise AssertionError(f"unexpected plugin entry point target: {entrypoints[0].value!r}")
if not callable(entrypoints[0].load()):
    raise AssertionError("plugin entry point did not load a callable")
"""
        result = subprocess.run(
            [
                sys.executable,
                "-P",
                "-c",
                script,
                self.version,
                self.description,
                str(self.root),
            ],
            cwd=tempfile.gettempdir(),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()

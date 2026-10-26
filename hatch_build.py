from pathlib import Path

from hatchling.metadata.plugin.interface import MetadataHookInterface
import yaml


class CustomMetadataHook(MetadataHookInterface):
    def update(self, metadata: dict) -> None:
        manifest = yaml.safe_load((Path(self.root) / "plugin.yaml").read_text(encoding="utf-8"))
        metadata["description"] = manifest["description"]

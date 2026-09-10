from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError
import yaml

from .errors import ConfigError
from .models import ZotWatchConfigV2
from .schema import validate_schema_document
from .semantic import resolve_feature_routes


class _UniqueKeySafeLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(loader: _UniqueKeySafeLoader, node: yaml.MappingNode,
                              deep: bool = False) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in result
        except TypeError as exc:
            raise ConfigError("CONFIG_SYNTAX", "mapping keys must be scalar") from exc
        if duplicate:
            raise ConfigError("CONFIG_SYNTAX", "duplicate mapping key")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _read_yaml(path: Path) -> Any:
    if not path.is_file():
        raise ConfigError("CONFIG_NOT_FOUND", "zotwatch.yaml was not found")
    try:
        with path.open("r", encoding="utf-8") as stream:
            data = yaml.load(stream, Loader=_UniqueKeySafeLoader)
    except ConfigError:
        raise
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ConfigError("CONFIG_SYNTAX", "zotwatch.yaml could not be parsed") from exc
    if data is None:
        data = {}
    return data


def load_v2_config(path: Path | str) -> ZotWatchConfigV2:
    data = _read_yaml(Path(path))
    validate_schema_document(data)
    try:
        config = ZotWatchConfigV2.model_validate(data)
    except ValidationError as exc:
        error = exc.errors(include_input=False, include_url=False)[0]
        pointer = "/" + "/".join(str(part) for part in error.get("loc", ()))
        raise ConfigError("CONFIG_SCHEMA", "configuration failed typed validation",
                          json_pointer=pointer) from exc
    resolve_feature_routes(config)
    return config


def load_v2_workspace(workspace: Path | str) -> ZotWatchConfigV2:
    return load_v2_config(Path(workspace) / "zotwatch.yaml")


__all__ = ["load_v2_config", "load_v2_workspace"]

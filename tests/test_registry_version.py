from importlib.util import module_from_spec, spec_from_file_location
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

import pytest

SCRIPT = Path(__file__).parents[1] / ".github" / "scripts" / "resolve_registry_version.py"
SPEC = spec_from_file_location("resolve_registry_version", SCRIPT)
assert SPEC and SPEC.loader
resolver = module_from_spec(SPEC)
SPEC.loader.exec_module(resolver)


def test_bootstraps_at_0_2_0_without_existing_versions() -> None:
    assert resolver.next_patch([]) == "0.2.0"
    assert resolver.next_patch(["latest", "sha-deadbeef"]) == "0.2.0"


def test_allocates_next_patch_after_highest_existing_version() -> None:
    assert resolver.next_patch(["0.2.0"]) == "0.2.1"
    assert resolver.next_patch(["0.2.0", "0.2.2", "latest"]) == "0.2.3"
    assert resolver.next_patch(["1.4.9", "0.2.0"]) == "1.4.10"


@pytest.mark.parametrize("tag", ["", "latest", "v0.2.0", "0.2", "bad tag", "-0.2.0"])
def test_rejects_non_semantic_explicit_tags(tag: str) -> None:
    with pytest.raises(ValueError):
        resolver.validate_explicit_tag(tag)


def test_accepts_unprefixed_semantic_explicit_tag() -> None:
    assert resolver.validate_explicit_tag("2.4.1") == "2.4.1"


def test_missing_registry_package_is_an_empty_tag_set() -> None:
    error = HTTPError(
        "https://ghcr.io/v2/rsocko/owl/tags/list",
        404,
        "Not Found",
        {},
        BytesIO(b'{"errors":[{"code":"NAME_UNKNOWN"}]}'),
    )
    with patch.object(resolver.urllib.request, "urlopen", side_effect=error):
        assert resolver.fetch_tags("https://ghcr.io", "rsocko/owl", {}) == []

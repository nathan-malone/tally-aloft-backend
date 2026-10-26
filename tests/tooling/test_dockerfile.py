import re
import tomllib
from typing import cast

from tests.support.paths import PYPROJECT, ROOT

IMAGE_REFERENCE = re.compile(r"^(?:FROM\s+|COPY\s+--from=)(\S+)", re.MULTILINE)
UV_IMAGE = re.compile(r"ghcr\.io/astral-sh/uv:(?P<version>[0-9.]+)@sha256:")
BASE_IMAGE = re.compile(r"^FROM python:(?P<version>\d+\.\d+)-", re.MULTILINE)


def _dockerfile() -> str:
    return (ROOT / "Dockerfile").read_text()


def _external_images() -> list[str]:
    """Return the images the Dockerfile pulls, leaving out its own stages."""
    references = IMAGE_REFERENCE.findall(_dockerfile())
    return [reference for reference in references if ":" in reference]


def _pyproject() -> dict[str, object]:
    return tomllib.loads(PYPROJECT.read_text())


def test_every_image_is_pinned_by_digest() -> None:
    images = _external_images()

    assert len(images) == 3
    assert [image for image in images if "@sha256:" not in image] == []


def test_uv_image_matches_the_required_uv_version() -> None:
    tool = cast("dict[str, dict[str, str]]", _pyproject()["tool"])
    required = tool["uv"]["required-version"].removeprefix("==")
    match = UV_IMAGE.search(_dockerfile())

    assert match is not None
    assert match.group("version") == required


def test_base_image_python_matches_requires_python() -> None:
    project = cast("dict[str, str]", _pyproject()["project"])
    requires = project["requires-python"].removeprefix("==").removesuffix(".*")

    assert set(BASE_IMAGE.findall(_dockerfile())) == {requires}

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from pathlib import PurePosixPath
from urllib.parse import urlparse

MIN_RELEASE_SAMPLES = 50
CONSENT_REFERENCE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,79}$")
FORBIDDEN_REFERENCE_MARKERS = (
    "dummy",
    "example",
    "fake",
    "placeholder",
    "sample",
    "test",
    "todo",
)


class DatasetPolicyError(ValueError):
    def __init__(self, violations: Iterable[str]) -> None:
        normalized = tuple(sorted(set(violations)))
        if not normalized:
            raise ValueError("dataset policy error requires at least one violation")
        self.violations = normalized
        super().__init__(f"release dataset policy failed: {','.join(normalized)}")


def private_object_key(reference: str) -> str:
    parsed = urlparse(reference)
    if parsed.scheme != "cos-private" or parsed.query or parsed.fragment:
        raise ValueError("asset reference must use cos-private:// without query data")
    raw_key = f"{parsed.netloc}{parsed.path}".lstrip("/")
    path = PurePosixPath(raw_key)
    if not raw_key or path.is_absolute() or ".." in path.parts:
        raise ValueError("asset reference contains an unsafe object key")
    return str(path)


def common_release_violations(
    *,
    domain: str,
    sample_ids: Sequence[str],
    dataset_versions: Sequence[str],
    splits: Sequence[str],
    consent_references: Sequence[str],
    tag_sets: Sequence[Sequence[str]],
    release_split: str,
) -> list[str]:
    violations: list[str] = []
    selected_count = sum(split == release_split for split in splits)
    if selected_count < MIN_RELEASE_SAMPLES:
        violations.append(f"{domain}.validation_sample_count_at_least_50")
    if len(sample_ids) != len(set(sample_ids)):
        violations.append(f"{domain}.sample_ids_unique")
    if len(set(dataset_versions)) != 1:
        violations.append(f"{domain}.single_dataset_version")
    if any(not _valid_consent_reference(reference) for reference in consent_references):
        violations.append(f"{domain}.authorization_references_valid")
    if any(len(tags) != len(set(tags)) for tags in tag_sets):
        violations.append(f"{domain}.tags_unique_per_sample")
    return violations


def private_reference_violations(
    *,
    domain: str,
    references: Sequence[str],
    require_unique: bool,
) -> list[str]:
    violations: list[str] = []
    try:
        keys = [private_object_key(reference) for reference in references]
    except ValueError:
        violations.append(f"{domain}.asset_references_private")
        return violations
    if require_unique and len(keys) != len(set(keys)):
        violations.append(f"{domain}.asset_references_unique")
    return violations


def tag_value(tags: Sequence[str], prefix: str) -> str | None:
    values = [tag.removeprefix(prefix) for tag in tags if tag.startswith(prefix)]
    if len(values) != 1 or not values[0]:
        return None
    return values[0]


def _valid_consent_reference(reference: str) -> bool:
    lowered = reference.lower()
    return bool(CONSENT_REFERENCE_PATTERN.fullmatch(reference)) and not any(
        marker in lowered for marker in FORBIDDEN_REFERENCE_MARKERS
    )

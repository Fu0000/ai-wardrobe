import stat
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest

from scripts.eval_bundle import (
    EXPECTED_FILES,
    EvalBundleError,
    extract_eval_bundle,
)


def write_bundle(path: Path, files: dict[str, bytes]) -> None:
    with ZipFile(path, mode="w", compression=ZIP_DEFLATED) as bundle:
        for name, content in files.items():
            bundle.writestr(name, content)


def valid_files() -> dict[str, bytes]:
    return {
        name: (b'{"samples":[]}\n' if name.endswith(".json") else b'{"sample_id":"x"}\n')
        for name in EXPECTED_FILES
    }


def test_extract_eval_bundle_accepts_only_the_contract_files(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "evals.zip"
    output = tmp_path / "extracted"
    files = valid_files()
    write_bundle(archive, files)

    extracted = extract_eval_bundle(archive, output)

    assert extracted == sorted(EXPECTED_FILES)
    for name, content in files.items():
        destination = output / name
        assert destination.read_bytes() == content
        assert stat.S_IMODE(destination.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    "unexpected_name",
    [
        "../outside.json",
        "/absolute.json",
        "diagnosis/extra.json",
    ],
)
def test_extract_eval_bundle_rejects_unexpected_paths(
    tmp_path: Path,
    unexpected_name: str,
) -> None:
    archive = tmp_path / "evals.zip"
    output = tmp_path / "extracted"
    files = valid_files()
    files[unexpected_name] = b"unsafe"
    write_bundle(archive, files)

    with pytest.raises(EvalBundleError):
        extract_eval_bundle(archive, output)

    assert not output.exists()


def test_extract_eval_bundle_rejects_symbolic_links(tmp_path: Path) -> None:
    archive = tmp_path / "evals.zip"
    output = tmp_path / "extracted"
    files = valid_files()
    symlink_name = "diagnosis/manifest.jsonl"
    with ZipFile(archive, mode="w", compression=ZIP_DEFLATED) as bundle:
        for name, content in files.items():
            if name != symlink_name:
                bundle.writestr(name, content)
        symlink = ZipInfo(symlink_name)
        symlink.create_system = 3
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        bundle.writestr(symlink, b"../../outside")

    with pytest.raises(EvalBundleError):
        extract_eval_bundle(archive, output)

    assert not output.exists()


def test_extract_eval_bundle_does_not_merge_into_existing_directory(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "evals.zip"
    output = tmp_path / "extracted"
    output.mkdir()
    sentinel = output / "sentinel"
    sentinel.write_text("preserve", encoding="utf-8")
    write_bundle(archive, valid_files())

    with pytest.raises(EvalBundleError):
        extract_eval_bundle(archive, output)

    assert sentinel.read_text(encoding="utf-8") == "preserve"

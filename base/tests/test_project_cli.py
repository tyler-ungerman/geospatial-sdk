from pathlib import Path

import pytest

from base.project_cli import create_plugin, main


def test_plugin_scaffold_has_buildable_package_entrypoint_test_and_pipeline(tmp_path):
    target = create_plugin("sample-map-plugin", tmp_path)
    assert target == tmp_path / "sample_map_plugin"
    assert (target / "pyproject.toml").is_file()
    assert (target / "src/sample_map_plugin/plugin.py").is_file()
    assert (target / "tests/test_plugin.py").is_file()
    assert (target / "examples/run_pipeline.py").is_file()
    pyproject = (target / "pyproject.toml").read_text()
    assert 'sample-map-plugin = "sample_map_plugin.plugin:setup"' in pyproject
    assert 'register_plugin(' in (target / "src/sample_map_plugin/plugin.py").read_text()
    assert "run_plugin(" in (target / "tests/test_plugin.py").read_text()


def test_plugin_scaffold_cli_and_no_overwrite(tmp_path, capsys):
    assert main(["plugin", "create", "cli-demo", "--path", str(tmp_path)]) == 0
    assert "Created plugin scaffold" in capsys.readouterr().out
    with pytest.raises(FileExistsError):
        create_plugin("cli-demo", tmp_path)


def test_hyphen_underscore_spellings_share_one_canonical_destination(tmp_path):
    first = create_plugin("review_plugin", tmp_path)
    assert first == tmp_path / "review_plugin"
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        create_plugin("review-plugin", tmp_path)
    assert [path.name for path in tmp_path.iterdir()] == ["review_plugin"]
    generated_readme = (first / "README.md").read_text()
    assert "./review_plugin[dev]" in generated_readme


@pytest.mark.parametrize("name", ["../escape", "bad name", "trailing-"])
def test_plugin_scaffold_rejects_invalid_names(tmp_path, name):
    with pytest.raises(ValueError):
        create_plugin(name, tmp_path)
    assert list(tmp_path.iterdir()) == []

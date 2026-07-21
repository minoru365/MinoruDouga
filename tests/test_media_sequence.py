from minoru_studio.cli import main
from minoru_studio.media_sequence import apply_renumbering, plan_renumbering


def _touch(tmp_path, name):
    path = tmp_path / name
    path.write_bytes(name.encode("utf-8"))
    return path


def test_plan_reverses_rank_and_zero_pads_within_a_date_group(tmp_path):
    _touch(tmp_path, "240731_0.png")
    _touch(tmp_path, "240731_1.mp4")
    _touch(tmp_path, "240731_2.jpg")

    plan = plan_renumbering(tmp_path)
    renamed = {item.original.name: item.renamed.name for item in plan}

    assert renamed == {
        "240731_0.png": "240731_02.png",
        "240731_1.mp4": "240731_01.mp4",
        "240731_2.jpg": "240731_00.jpg",
    }


def test_plan_groups_dates_independently(tmp_path):
    _touch(tmp_path, "240731_0.png")
    _touch(tmp_path, "240731_1.jpg")
    _touch(tmp_path, "240801_0.png")
    _touch(tmp_path, "240801_1.png")

    plan = plan_renumbering(tmp_path)
    renamed = {item.original.name: item.renamed.name for item in plan}

    assert renamed == {
        "240731_0.png": "240731_01.png",
        "240731_1.jpg": "240731_00.jpg",
        "240801_0.png": "240801_01.png",
        "240801_1.png": "240801_00.png",
    }


def test_plan_widens_padding_for_double_digit_groups(tmp_path):
    for number in range(11):
        _touch(tmp_path, f"240917_{number}.jpg")

    plan = plan_renumbering(tmp_path)
    renamed = {item.original.name: item.renamed.name for item in plan}

    assert renamed["240917_0.jpg"] == "240917_10.jpg"
    assert renamed["240917_10.jpg"] == "240917_00.jpg"


def test_plan_ignores_non_matching_and_subfolder_files(tmp_path):
    _touch(tmp_path, "240731_0.png")
    _touch(tmp_path, "240731_1.jpg")
    _touch(tmp_path, "notes.txt")
    _touch(tmp_path, "IMG_0001.jpg")
    (tmp_path / "sub").mkdir()
    _touch(tmp_path / "sub", "240731_0.png")

    plan = plan_renumbering(tmp_path)
    touched = {item.original.name for item in plan}

    assert touched == {"240731_0.png", "240731_1.jpg"}


def test_apply_performs_a_safe_permutation_rename(tmp_path):
    a = _touch(tmp_path, "240731_0.png")
    b = _touch(tmp_path, "240731_1.mp4")
    c = _touch(tmp_path, "240731_2.jpg")
    a.write_bytes(b"png-bytes")
    b.write_bytes(b"mp4-bytes")
    c.write_bytes(b"jpg-bytes")

    plan = plan_renumbering(tmp_path)
    apply_renumbering(plan)

    assert (tmp_path / "240731_02.png").read_bytes() == b"png-bytes"
    assert (tmp_path / "240731_01.mp4").read_bytes() == b"mp4-bytes"
    assert (tmp_path / "240731_00.jpg").read_bytes() == b"jpg-bytes"
    assert not a.exists() and not b.exists() and not c.exists()


def test_reversal_is_not_idempotent_running_twice_undoes_it(tmp_path):
    # The tool has no way to know whether a folder was already fixed; it
    # always reverses whatever ordering it currently finds. Running it a
    # second time on an already-fixed folder flips it back to the
    # original (wrong) order, so callers must not re-run it blindly.
    _touch(tmp_path, "240731_0.png")
    _touch(tmp_path, "240731_1.jpg")

    apply_renumbering(plan_renumbering(tmp_path))
    assert (tmp_path / "240731_00.jpg").exists()
    assert (tmp_path / "240731_01.png").exists()

    apply_renumbering(plan_renumbering(tmp_path))
    assert (tmp_path / "240731_00.png").exists()
    assert (tmp_path / "240731_01.jpg").exists()


def test_cli_dry_run_previews_without_renaming(tmp_path, capsys):
    _touch(tmp_path, "240731_0.png")
    _touch(tmp_path, "240731_1.jpg")

    assert main(["renumber-sequence", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "240731_0.png -> 240731_01.png" in out
    assert "Pass -Apply" in out
    assert (tmp_path / "240731_0.png").exists()
    assert (tmp_path / "240731_1.jpg").exists()


def test_cli_apply_actually_renames(tmp_path, capsys):
    _touch(tmp_path, "240731_0.png")
    _touch(tmp_path, "240731_1.jpg")

    assert main(["renumber-sequence", str(tmp_path), "-Apply"]) == 0
    out = capsys.readouterr().out
    assert "renamed 2 file(s)." in out
    assert (tmp_path / "240731_00.jpg").exists()
    assert (tmp_path / "240731_01.png").exists()


def test_cli_reports_invalid_folder(tmp_path, capsys):
    missing = tmp_path / "does-not-exist"
    assert main(["renumber-sequence", str(missing)]) == 1
    assert capsys.readouterr().err.strip()

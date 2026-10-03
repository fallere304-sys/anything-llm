from pathlib import Path

from buddy.bootstrap import bundle_dir, prepare_home


def test_prepare_home_copies_once_and_never_overwrites(tmp_path):
    bundle = bundle_dir()
    assert (bundle / ".env.example").is_file() and (bundle / "prompts" / "system.md").is_file()
    created = prepare_home(tmp_path, bundle)
    assert sorted(created) == [".env", ".env.example", "prompts/system.md"]
    (tmp_path / ".env").write_text("GEMINI_API_KEY=mine\n", encoding="utf-8")
    assert prepare_home(tmp_path, bundle) == []  # 2回目は何も作らない
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "GEMINI_API_KEY=mine\n"  # 利用者の設定は上書きしない


def test_prepare_home_skips_missing_sources(tmp_path):
    assert prepare_home(tmp_path / "home", tmp_path / "empty-bundle") == []

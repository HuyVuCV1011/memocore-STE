from memocore.cli.main import _parse_args, _print_models
from memocore.config import Settings


def test_cli_defaults_to_run():
    args = _parse_args([])

    assert args.command == "run"
    assert args.provider is None
    assert args.model is None


def test_cli_accepts_provider_and_model():
    args = _parse_args(["run", "--provider", "gemini", "--model", "gemini-test"])

    assert args.command == "run"
    assert args.provider == "gemini"
    assert args.model == "gemini-test"


def test_models_command_marks_configured_provider(monkeypatch, capsys):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_OWNER_ID", "9001")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")

    _print_models(Settings(_env_file=None))

    output = capsys.readouterr().out
    assert "- gemini: gemini-2.5-flash (key set)" in output
    assert "- groq: llama-3.3-70b-versatile (key missing)" in output

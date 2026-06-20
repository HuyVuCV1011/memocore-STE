from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

from memocore.app import create_app, shutdown_app
from memocore.adapters.llm.provider_factory import PROVIDER_DEFAULTS
from memocore.config import Settings, get_settings


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv)
    settings = get_settings()
    if args.command == "models":
        _print_models(settings)
        return
    settings = settings.with_model_override(provider=args.provider, name=args.model)
    asyncio.run(_run(settings))


async def _run(settings: Settings) -> None:
    app = await create_app(settings)
    await app.initialize()
    if app.post_init:
        await app.post_init(app)
    try:
        await app.start()
        if app.updater is None:
            raise RuntimeError("Telegram updater is not configured")
        await app.updater.start_polling()
        await asyncio.Event().wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        if app.updater and app.updater.running:
            await app.updater.stop()
        if app.running:
            await app.stop()
        await app.shutdown()
        await shutdown_app(app)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the MemoCore personal secretary.")
    subparsers = parser.add_subparsers(dest="command")
    run_parser = subparsers.add_parser("run", help="Start the Telegram secretary.")
    run_parser.add_argument("--provider", choices=sorted(PROVIDER_DEFAULTS))
    run_parser.add_argument("--model", help="Override the selected provider's default model.")
    subparsers.add_parser("models", help="List available provider profiles.")
    args = parser.parse_args(argv)
    if args.command is None:
        args.command = "run"
        args.provider = None
        args.model = None
    return args


def _print_models(settings: Settings) -> None:
    print(f"Current: {settings.model.provider} / {settings.model.name}")
    print("")
    print("Provider profiles:")
    for provider, (_, default_model, _) in PROVIDER_DEFAULTS.items():
        if provider == "ollama":
            availability = "local"
        else:
            availability = "key set" if settings.api_key_for_provider(provider) else "key missing"
        print(f"- {provider}: {default_model} ({availability})")


if __name__ == "__main__":
    main()

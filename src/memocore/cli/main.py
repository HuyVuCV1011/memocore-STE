from __future__ import annotations

import asyncio

from memocore.app import create_app, shutdown_app


def main() -> None:
    asyncio.run(_run())


async def _run() -> None:
    app = await create_app()
    await app.initialize()
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


if __name__ == "__main__":
    main()

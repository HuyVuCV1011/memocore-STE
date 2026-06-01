from telegram.ext import Application, ApplicationBuilder

from memocore.adapters.telegram.handlers import register_handlers
from memocore.services.capture_service import CaptureService
from memocore.services.secretary_service import SecretaryService


def create_bot(
    token: str, capture_service: CaptureService, secretary_service: SecretaryService
) -> Application:
    app = ApplicationBuilder().token(token).build()
    register_handlers(app, capture_service, secretary_service)
    return app

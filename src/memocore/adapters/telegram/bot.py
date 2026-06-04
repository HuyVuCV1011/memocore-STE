from telegram.ext import Application, ApplicationBuilder

from memocore.adapters.telegram.handlers import register_handlers
from memocore.services.capture_service import CaptureService
from memocore.services.clarification_service import ClarificationService
from memocore.services.conversation_service import ConversationService
from memocore.services.secretary_service import SecretaryService


def create_bot(
    token: str,
    capture_service: CaptureService,
    secretary_service: SecretaryService,
    clarification_service: ClarificationService | None = None,
    conversation_service: ConversationService | None = None,
) -> Application:
    app = ApplicationBuilder().token(token).build()
    register_handlers(
        app,
        capture_service,
        secretary_service,
        clarification_service,
        conversation_service,
    )
    return app

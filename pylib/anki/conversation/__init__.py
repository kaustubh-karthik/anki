# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

from __future__ import annotations

from .collocations import DEFAULT_KO_COLLOCATIONS, select_collocation_targets
from .export import export_conversation_telemetry
from .gateway import (
    ConversationGateway,
    ConversationProvider,
    OpenAIConversationProvider,
)
from .glossary import lookup_gloss, rebuild_glossary_from_snapshot
from .grammar import DEFAULT_KO_GRAMMAR, select_grammar_patterns
from .keys import read_api_key_file, resolve_openai_api_key
from .local_provider import LocalConversationProvider
from .openai import OpenAIResponsesJsonClient
from .plan_reply import (
    FakePlanReplyProvider,
    OpenAIPlanReplyProvider,
    PlanReplyGateway,
    PlanReplyProvider,
    PlanReplyRequest,
)
from .planner import ConversationPlanner, PlannerState
from .redaction import redact_text
from .session import ConversationSession
from .settings import (
    CONFIG_KEY,
    ConversationSettings,
    RedactionLevel,
    load_conversation_settings,
    save_conversation_settings,
)
from .snapshot import DeckSnapshot, build_deck_snapshot
from .suggest import apply_suggested_cards, suggestions_from_wrap
from .telemetry import ConversationTelemetryStore
from .translate import (
    LocalTranslateProvider,
    OpenAITranslateProvider,
    TranslateGateway,
    TranslateRequest,
    TranslateResponse,
)
from .types import (
    ConversationRequest,
    ConversationResponse,
    GenerationInstructions,
    LanguageConstraints,
)
from .wrap import compute_session_wrap

__all__ = [
    "ConversationGateway",
    "ConversationPlanner",
    "ConversationProvider",
    "ConversationRequest",
    "ConversationResponse",
    "ConversationSettings",
    "ConversationTelemetryStore",
    "DEFAULT_KO_COLLOCATIONS",
    "DEFAULT_KO_GRAMMAR",
    "DeckSnapshot",
    "GenerationInstructions",
    "LanguageConstraints",
    "lookup_gloss",
    "FakePlanReplyProvider",
    "OpenAIConversationProvider",
    "PlannerState",
    "PlanReplyGateway",
    "PlanReplyProvider",
    "PlanReplyRequest",
    "OpenAIPlanReplyProvider",
    "OpenAIResponsesJsonClient",
    "read_api_key_file",
    "resolve_openai_api_key",
    "LocalConversationProvider",
    "ConversationSession",
    "apply_suggested_cards",
    "build_deck_snapshot",
    "compute_session_wrap",
    "check_response_against_request",
    "export_conversation_telemetry",
    "redact_text",
    "RedactionLevel",
    "CONFIG_KEY",
    "load_conversation_settings",
    "save_conversation_settings",
    "rebuild_glossary_from_snapshot",
    "select_collocation_targets",
    "select_grammar_patterns",
    "suggestions_from_wrap",
    "LocalTranslateProvider",
    "OpenAITranslateProvider",
    "TranslateGateway",
    "TranslateRequest",
    "TranslateResponse",
]
from .contract import check_response_against_request

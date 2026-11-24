"""Send an emergency notification; related files: config_flow.py and __init__.py."""

import asyncio
import logging
import re
import unicodedata

from homeassistant.components.conversation.chat_log import UserContent, current_chat_log
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
_ACTION = re.compile(r"notify\.mobile_app_[a-z0-9_]+")
_REQUEST = re.compile(
    r"(?:제발\s*)?(?:저를\s*|나를\s*|나\s*)?"
    r"(?:살려|구해|도와)\s*(?:줘요?|주세요|주십시오)[!.…]*|s\.?o\.?s[!.…]*",
    re.IGNORECASE,
)
_TITLE = "긴급 도움 요청"
_MESSAGE = "Assist에서 긴급 도움 요청이 감지되었습니다. 즉시 확인해 주세요."


def mobile_app_actions(hass: HomeAssistant) -> list[str]:
    """List registered Companion App notification actions for the config flow."""
    return sorted(
        action
        for service in hass.services.async_services_for_domain("notify")
        if _ACTION.fullmatch(action := f"notify.{service}")
    )


class EmergencyTool(llm.Tool):
    """Send a fixed notification to the device selected by the administrator."""

    name = "assist_emergency__notify"
    title = _TITLE
    description = "Send an emergency notification for a direct Korean plea for rescue or SOS."
    integration = DOMAIN
    annotations = llm.ToolAnnotations(destructive=False)

    def __init__(self, action: str) -> None:
        self._action = action
        self._lock = asyncio.Lock()
        self._submitted = False

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> llm.ToolResult:
        """Validate the Assist transcript and prevent duplicate calls within this turn."""
        chat_log = current_chat_log.get()
        latest_user = (
            next(
                (item for item in reversed(chat_log.content) if isinstance(item, UserContent)), None
            )
            if chat_log is not None
            else None
        )
        if (
            tool_input.external
            or latest_user is None
            or not _REQUEST.fullmatch(unicodedata.normalize("NFKC", latest_user.content).strip())
        ):
            return llm.ToolResult(data={"error": "직접적인 긴급 도움 요청이 아닙니다."}, error=True)

        async with self._lock:
            if self._submitted:
                return llm.ToolResult(data={"status": "already_submitted"})
            service = self._action.removeprefix("notify.")
            if not _ACTION.fullmatch(self._action) or not hass.services.has_service(
                "notify", service
            ):
                _LOGGER.warning("Configured Companion App notification action is unavailable")
                return llm.ToolResult(
                    data={"error": "설정한 긴급 연락 대상의 알림 액션을 사용할 수 없습니다."},
                    error=True,
                )
            try:
                await hass.services.async_call(
                    "notify",
                    service,
                    {"title": _TITLE, "message": _MESSAGE},
                    blocking=True,
                    context=llm_context.context,
                )
            except Exception:
                _LOGGER.exception("Emergency notification action failed")
                return llm.ToolResult(data={"error": "긴급 알림 요청에 실패했습니다."}, error=True)
            self._submitted = True
            return llm.ToolResult(data={"status": "submitted"})


class AssistEmergencyAPI(llm.API):
    """Expose emergency notification as a selectable Assist provider."""

    def __init__(self, hass: HomeAssistant, action: str) -> None:
        super().__init__(hass=hass, id="assist_emergency_contact", name="Assist Emergency Contact")
        self._action = action

    async def async_get_api_instance(self, llm_context: llm.LLMContext) -> llm.APIInstance:
        """Give the agent a turn-scoped tool for direct rescue requests."""
        return llm.APIInstance(
            api=self,
            api_prompt=(
                "When the latest user message is a direct plea for rescue such as "
                "살려줘, 구해줘, 제발 살려줘, 도와주세요, or SOS, immediately call "
                "assist_emergency__notify once without asking for confirmation. "
                "The tool verifies the original user message. Do not call for quoted, hypothetical, negated, "
                "or ordinary requests for assistance. Report the actual tool result. A submitted "
                "result means Home Assistant accepted the call, not that a phone received it."
            ),
            llm_context=llm_context,
            tools=[EmergencyTool(self._action)],
        )

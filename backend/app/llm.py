import asyncio
import logging
from dataclasses import dataclass
from time import perf_counter
from uuid import uuid4

from openai import APIConnectionError, APIStatusError, AsyncOpenAI

from .config import Settings
from .logging_config import log_event

logger = logging.getLogger("agentic_flow.llm")


class LMStudioError(RuntimeError):
    """Raised when the local LM Studio server cannot satisfy a request."""


@dataclass(frozen=True)
class ModelStatus:
    connected: bool
    model: str | None
    detail: str | None = None


class LMStudioClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = AsyncOpenAI(
            base_url=settings.lmstudio_base_url.rstrip("/") + "/",
            api_key=settings.lmstudio_api_key,
            timeout=120,
            max_retries=1,
        )
        self._model: str | None = settings.lmstudio_model or None
        self._model_lock = asyncio.Lock()
        log_event(
            logger,
            logging.INFO,
            "lmstudio.client.initialized",
            "LM Studio client initialized",
            base_url=settings.lmstudio_base_url,
            configured_model=self._model,
            timeout_seconds=120,
            max_retries=1,
        )

    async def resolve_model(self) -> str:
        if self._model:
            log_event(
                logger,
                logging.DEBUG,
                "lmstudio.model.cache_hit",
                "Using cached LM Studio model",
                model=self._model,
            )
            return self._model

        async with self._model_lock:
            if self._model:
                return self._model
            started = perf_counter()
            log_event(
                logger,
                logging.INFO,
                "lmstudio.model.discovery_started",
                "Discovering loaded models from LM Studio",
                base_url=self.settings.lmstudio_base_url,
            )
            try:
                models = await self.client.models.list()
            except (APIConnectionError, APIStatusError, OSError) as exc:
                log_event(
                    logger,
                    logging.ERROR,
                    "lmstudio.model.discovery_failed",
                    "LM Studio model discovery failed",
                    exc_info=True,
                    duration_ms=round((perf_counter() - started) * 1000, 2),
                    error_type=type(exc).__name__,
                    base_url=self.settings.lmstudio_base_url,
                )
                raise LMStudioError(self._connection_message(exc)) from exc
            if not models.data:
                log_event(
                    logger,
                    logging.WARNING,
                    "lmstudio.model.none_loaded",
                    "LM Studio responded but no models are loaded",
                    duration_ms=round((perf_counter() - started) * 1000, 2),
                )
                raise LMStudioError(
                    "LM Studio is running, but no model is loaded. Load a chat model and retry."
                )
            self._model = models.data[0].id
            log_event(
                logger,
                logging.INFO,
                "lmstudio.model.discovery_completed",
                "Loaded LM Studio model selected",
                duration_ms=round((perf_counter() - started) * 1000, 2),
                selected_model=self._model,
                available_models=[model.id for model in models.data],
                model_count=len(models.data),
            )
            return self._model

    async def status(self) -> ModelStatus:
        started = perf_counter()
        try:
            model = await self.resolve_model()
            log_event(
                logger,
                logging.DEBUG,
                "lmstudio.health.connected",
                "LM Studio health check succeeded",
                model=model,
                duration_ms=round((perf_counter() - started) * 1000, 2),
            )
            return ModelStatus(connected=True, model=model)
        except LMStudioError as exc:
            log_event(
                logger,
                logging.WARNING,
                "lmstudio.health.unavailable",
                "LM Studio health check failed",
                detail=str(exc),
                duration_ms=round((perf_counter() - started) * 1000, 2),
            )
            return ModelStatus(connected=False, model=None, detail=str(exc))

    async def complete(
        self,
        *,
        system: str,
        user: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_schema: dict | None = None,
        schema_name: str = "structured_response",
    ) -> str:
        model = await self.resolve_model()
        call_id = str(uuid4())
        started = perf_counter()
        resolved_temperature = (
            temperature if temperature is not None else self.settings.agent_temperature
        )
        resolved_max_tokens = max_tokens or self.settings.agent_max_tokens
        request: dict = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": resolved_temperature,
            "max_tokens": resolved_max_tokens,
        }
        if response_schema:
            request["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": response_schema,
                },
            }
        request_details = {
            "call_id": call_id,
            "model": model,
            "temperature": resolved_temperature,
            "max_tokens": resolved_max_tokens,
            "structured_output": response_schema is not None,
            "schema_name": schema_name if response_schema else None,
            "system_chars": len(system),
            "user_chars": len(user),
        }
        if self.settings.log_include_content:
            request_details.update(
                {
                    "system_prompt": system,
                    "user_prompt": user,
                    "response_schema": response_schema,
                }
            )
        log_event(
            logger,
            logging.DEBUG,
            "lmstudio.completion.started",
            "Sending chat completion to LM Studio",
            **request_details,
        )
        try:
            response = await self.client.chat.completions.create(**request)
        except (APIConnectionError, APIStatusError, OSError) as exc:
            error_details = {
                **request_details,
                "duration_ms": round((perf_counter() - started) * 1000, 2),
                "error_type": type(exc).__name__,
            }
            if isinstance(exc, APIStatusError):
                error_details["status_code"] = exc.status_code
                error_details["response_body"] = str(exc.body)
            log_event(
                logger,
                logging.ERROR,
                "lmstudio.completion.failed",
                "LM Studio chat completion failed",
                exc_info=True,
                **error_details,
            )
            raise LMStudioError(self._connection_message(exc)) from exc

        content = response.choices[0].message.content if response.choices else None
        if not content:
            log_event(
                logger,
                logging.ERROR,
                "lmstudio.completion.empty",
                "LM Studio returned no completion content",
                call_id=call_id,
                model=model,
                duration_ms=round((perf_counter() - started) * 1000, 2),
                response_id=response.id,
                choice_count=len(response.choices),
            )
            raise LMStudioError("The local model returned an empty response.")
        stripped_content = content.strip()
        choice = response.choices[0]
        response_details = {
            "call_id": call_id,
            "model": model,
            "response_id": response.id,
            "duration_ms": round((perf_counter() - started) * 1000, 2),
            "finish_reason": choice.finish_reason,
            "response_chars": len(stripped_content),
            "usage": response.usage.model_dump() if response.usage else None,
        }
        if self.settings.log_include_content:
            response_details["response_content"] = stripped_content
        log_event(
            logger,
            logging.DEBUG,
            "lmstudio.completion.completed",
            "LM Studio chat completion succeeded",
            **response_details,
        )
        return stripped_content

    def _connection_message(self, exc: Exception) -> str:
        return (
            f"Cannot reach a usable LM Studio model at {self.settings.lmstudio_base_url}. "
            "Start the LM Studio local server, load a chat model, and retry. "
            f"Details: {exc}"
        )

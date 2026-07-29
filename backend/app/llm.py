import asyncio
from dataclasses import dataclass

from openai import APIConnectionError, APIStatusError, AsyncOpenAI

from .config import Settings


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

    async def resolve_model(self) -> str:
        if self._model:
            return self._model

        async with self._model_lock:
            if self._model:
                return self._model
            try:
                models = await self.client.models.list()
            except (APIConnectionError, APIStatusError, OSError) as exc:
                raise LMStudioError(self._connection_message(exc)) from exc
            if not models.data:
                raise LMStudioError(
                    "LM Studio is running, but no model is loaded. Load a chat model and retry."
                )
            self._model = models.data[0].id
            return self._model

    async def status(self) -> ModelStatus:
        try:
            model = await self.resolve_model()
            return ModelStatus(connected=True, model=model)
        except LMStudioError as exc:
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
        request: dict = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": (
                temperature if temperature is not None else self.settings.agent_temperature
            ),
            "max_tokens": max_tokens or self.settings.agent_max_tokens,
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
        try:
            response = await self.client.chat.completions.create(**request)
        except (APIConnectionError, APIStatusError, OSError) as exc:
            raise LMStudioError(self._connection_message(exc)) from exc

        content = response.choices[0].message.content if response.choices else None
        if not content:
            raise LMStudioError("The local model returned an empty response.")
        return content.strip()

    def _connection_message(self, exc: Exception) -> str:
        return (
            f"Cannot reach a usable LM Studio model at {self.settings.lmstudio_base_url}. "
            "Start the LM Studio local server, load a chat model, and retry. "
            f"Details: {exc}"
        )

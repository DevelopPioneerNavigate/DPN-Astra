"""Provider-independent contracts for local inference adapters."""
from typing import Literal, NotRequired, Protocol, TypedDict


class ModelError(RuntimeError):
    """Inference failed; callers must not treat it as a generated answer."""


class ChatMessage(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


class ModelHealth(TypedDict):
    connected: bool
    model_installed: bool
    installed_models: list[str]
    error: NotRequired[str]


class InferenceProvider(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...

    def health(self) -> ModelHealth: ...

    def chat(self, messages: list[ChatMessage]) -> str: ...

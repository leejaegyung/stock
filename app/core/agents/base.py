from abc import ABC, abstractmethod

from anthropic import Anthropic


class BaseAgent(ABC):
    model: str  # set by subclass

    def __init__(self, client: Anthropic) -> None:
        self._client = client

    @property
    @abstractmethod
    def system_prompt(self) -> str: ...

    def _call(self, user_message: str, max_tokens: int = 4096) -> str:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=self.system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        return response.content[0].text

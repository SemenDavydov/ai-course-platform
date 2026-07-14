import httpx
import json
import ssl
import certifi
from typing import Any, Optional, AsyncGenerator
from aiogram.client.session.base import BaseSession
from aiogram.client.default import Default
from aiogram.methods import TelegramMethod
from aiogram.methods.base import TelegramType
from aiogram.client.bot import Bot
from aiogram.types import InputFile


class HttpxSession(BaseSession):
    def __init__(self):
        super().__init__()
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            ssl_ctx = ssl.create_default_context(cafile=certifi.where())
            self._client = httpx.AsyncClient(verify=ssl_ctx, timeout=60.0)
        return self._client

    def _resolve_defaults(self, data: dict, bot: Bot) -> dict:
        """Заменяет объекты Default на реальные значения из bot.default"""
        resolved = {}
        for key, value in data.items():
            if isinstance(value, Default):
                # Берём значение из bot.default по имени поля
                if bot.default and hasattr(bot.default, key):
                    real_value = getattr(bot.default, key)
                    if real_value is not None and not isinstance(real_value, Default):
                        resolved[key] = real_value
                # Если реального значения нет — просто пропускаем поле
            elif isinstance(value, dict):
                resolved[key] = self._resolve_defaults(value, bot)
            else:
                resolved[key] = value
        return resolved

    async def make_request(
        self, bot: Bot, method: TelegramMethod[TelegramType], timeout: Optional[int] = None
    ) -> TelegramType:
        client = await self._get_client()
        url = self.api.api_url(token=bot.token, method=method.__api_method__)

        data = method.model_dump(exclude_none=True)
        data = self._resolve_defaults(data, bot)

        # Проверяем наличие файлов
        has_files = False
        files = {}
        form = {}

        for key, value in list(data.items()):
            if isinstance(value, InputFile):
                has_files = True
                files[key] = (value.filename or key, value.read(bot), value.content_type)
                del data[key]
            elif isinstance(value, (dict, list)):
                form[key] = json.dumps(value)
            else:
                form[key] = str(value)

        if has_files:
            resp = await client.post(url, data=form, files=files, timeout=timeout or 60)
        else:
            resp = await client.post(url, json=data, timeout=timeout or 60)

        response = self.check_response(
            bot=bot,
            method=method,
            status_code=resp.status_code,
            content=resp.content,
        )
        return response.result

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def stream_content(
        self, url: str, headers: Optional[dict] = None,
        timeout: int = 30, chunk_size: int = 65536,
        raise_for_status: bool = True
    ) -> AsyncGenerator[bytes, None]:
        client = await self._get_client()
        async with client.stream("GET", url, headers=headers, timeout=timeout) as resp:
            async for chunk in resp.aiter_bytes(chunk_size):
                yield chunk
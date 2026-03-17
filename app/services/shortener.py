import httpx
from typing import Optional
from app.config import settings


class URLShortener:
    """Сервис для сокращения ссылок через clc.li API"""

    def __init__(self):
        self.api_key = settings.CLC_API_KEY
        self.base_url = "https://clc.li/api"
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

    async def shorten(self, long_url: str, custom_alias: Optional[str] = None) -> Optional[str]:
        """
        Сокращает длинную ссылку

        Args:
            long_url: Длинная ссылка для сокращения
            custom_alias: Желаемый алиас (если есть)

        Returns:
            Короткая ссылка или None в случае ошибки
        """
        payload = {
            "url": long_url,
            "status": "public"
        }

        if custom_alias:
            payload["custom"] = custom_alias

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/url/add",
                    headers=self.headers,
                    json=payload,
                    timeout=10.0
                )

                if response.status_code == 200:
                    data = response.json()
                    if data.get("error") == 0:
                        short_url = data.get("shorturl")
                        print(f"✅ Ссылка сокращена: {long_url} -> {short_url}")
                        return short_url
                    else:
                        print(f"❌ Ошибка API clc.li: {data.get('message')}")
                else:
                    print(f"❌ HTTP ошибка: {response.status_code}")

                return None

        except Exception as e:
            print(f"❌ Исключение при сокращении ссылки: {e}")
            return None

    async def shorten_batch(self, urls: list) -> dict:
        """
        Сокращает несколько ссылок (с ограничением скорости)
        Возвращает словарь {оригинальная_ссылка: короткая_ссылка}
        """
        result = {}
        for url in urls:
            short = await self.shorten(url)
            result[url] = short
            # Небольшая задержка, чтобы не превысить лимит API
            import asyncio
            await asyncio.sleep(0.5)
        return result
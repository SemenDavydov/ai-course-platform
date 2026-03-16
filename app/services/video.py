import httpx
import jwt
from datetime import datetime, timedelta, time
from typing import Optional
from app.config import settings
from app.models.user import User


class VideoService:
    """Сервис для работы с видеохостингом (Kinescope)"""

    def __init__(self):
        self.api_key = settings.KINESCOPE_API_KEY
        self.base_url = "https://api.kinescope.io/v1"
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

    async def generate_watermarked_link(
            self,
            user: User,
            video_id: str,
            lifetime: int = None
    ) -> Optional[str]:
        """
        Генерирует ссылку на видео с водяным знаком.
        Использует готовый play_link из Kinescope с добавлением параметров
        """
        if lifetime is None:
            lifetime = settings.VIDEO_LINK_LIFETIME

        # Получаем информацию о видео
        async with httpx.AsyncClient() as client:
            try:
                # Сначала получаем видео, чтобы узнать его play_link
                response = await client.get(
                    f"{self.base_url}/videos/{video_id}",
                    headers=self.headers
                )

                if response.status_code != 200:
                    print(f"Error getting video info: {response.text}")
                    return None

                video_data = response.json()
                play_link = video_data.get("data", {}).get("play_link")

                if not play_link:
                    return None

                # Водяной знак добавляется через параметры в URL
                watermark_text = f"{user.email or user.telegram_id}"

                # Добавляем параметры для водяного знака (если поддерживается)
                # Формируем ссылку с параметрами
                from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

                parsed = urlparse(play_link)
                params = parse_qs(parsed.query)
                params['watermark'] = [watermark_text]
                params['expires'] = [str(int(time.time()) + lifetime)]

                new_query = urlencode(params, doseq=True)
                new_parts = list(parsed)
                new_parts[4] = new_query

                return urlunparse(new_parts)

            except Exception as e:
                print(f"Error generating video link: {e}")
                return None

    async def generate_jwt_link(self, user: User, video_id: str) -> str:
        """
        Альтернативный метод: генерируем свою JWT-ссылку
        (если видеохостинг поддерживает JWT-авторизацию)
        """
        payload = {
            "user_id": user.id,
            "telegram_id": user.telegram_id,
            "email": user.email,
            "video_id": video_id,
            "exp": datetime.utcnow() + timedelta(seconds=settings.VIDEO_LINK_LIFETIME)
        }

        token = jwt.encode(
            payload,
            settings.SECRET_KEY,
            algorithm="HS256"
        )

        # Формируем ссылку на наш прокси-эндпоинт
        # FastAPI будет проверять токен и редиректить на видео
        return f"/api/video/{video_id}?token={token}"

    async def get_direct_video_url(self, video_id: str) -> Optional[str]:
        """
        Получает прямую ссылку на видео для встраивания
        """
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"{self.base_url}/videos/{video_id}",
                    headers=self.headers
                )

                if response.status_code == 200:
                    data = response.json()
                    # Берем HLS ссылку для потокового видео
                    return data.get("data", {}).get("hls_link")
                else:
                    print(f"Error getting video: {response.text}")
                    return None
            except Exception as e:
                print(f"Exception in get_direct_video_url: {e}")
                return None


    async def get_embed_link(self, video_id: str) -> Optional[str]:
        """
        Получает embed-ссылку на видео из Kinescope
        """
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"{self.base_url}/videos/{video_id}",
                    headers=self.headers
                )

                if response.status_code == 200:
                    data = response.json()
                    return data.get("data", {}).get("embed_link")
                else:
                    print(f"Error getting embed link: {response.text}")
                    return None
            except Exception as e:
                print(f"Exception in get_embed_link: {e}")
                return None
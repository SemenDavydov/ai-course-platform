import asyncio
import socket
import aiohttp
from aiohttp import TCPConnector

# Заставляем aiohttp использовать стандартный DNS-резолвер Python
class SafeResolver(aiohttp.DefaultResolver):
    async def resolve(self, host, port=0, family=socket.AF_INET):
        # Используем синхронный резолвер через getaddrinfo
        infos = await asyncio.get_event_loop().run_in_executor(
            None, socket.getaddrinfo, host, port, family, socket.SOCK_STREAM
        )
        result = []
        for family, _, _, _, address in infos:
            result.append({
                'hostname': host,
                'host': address[0],
                'port': address[1],
                'family': family,
                'proto': 0,
                'flags': socket.AI_NUMERICHOST,
            })
        return result

async def test():
    connector = TCPConnector(resolver=SafeResolver(), force_close=True)
    async with aiohttp.ClientSession(connector=connector) as session:
        async with session.get("https://api.telegram.org") as resp:
            print(f"Status: {resp.status}")

asyncio.run(test())
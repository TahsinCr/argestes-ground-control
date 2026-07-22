"""Başarılı kilit bitiminde POST /api/kilitlenme_bilgisi atan asenkron iş parçası."""

import aiohttp
import asyncio


class SunucuClient:
    def __init__(self, base_url):
        self.base_url = base_url

    async def send_lock_info(self, data):
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{self.base_url}/api/kilitlenme_bilgisi", json=data) as resp:
                return await resp.text()

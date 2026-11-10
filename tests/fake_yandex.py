"""A fake Yandex Bot API on ``aiohttp.test_utils.TestServer`` that records every request."""
import json

from aiohttp import web
from aiohttp.test_utils import TestServer


class FakeYandex:
    def __init__(self):
        self.requests = []
        self.responses = {}  # path -> dict | callable(request, record) | list of those (consumed in order)
        self.counter = 1000
        self.app = web.Application()
        self.app.router.add_route("*", "/bot/v1/{tail:.*}", self._handle)
        self.app.router.add_get("/cdn/blob", self._cdn)
        self.server = None

    async def _cdn(self, request):
        return web.Response(body=b"CDN")

    async def _handle(self, request: web.Request):
        tail = request.match_info["tail"].strip("/")
        record = {"method": request.method, "path": tail, "query": dict(request.query), "fields": []}
        if request.content_type == "multipart/form-data":
            async for part in await request.multipart():
                record["fields"].append((part.name, part.filename, bytes(await part.read())))
        else:
            raw = await request.read()
            if request.content_type == "application/json" and raw:
                record["json"] = json.loads(raw)
        self.requests.append(record)
        handler = self.responses.get(tail)
        if isinstance(handler, list):
            handler = handler.pop(0) if len(handler) > 1 else handler[0]
        if handler is None:
            self.counter += 1
            if tail in ("messages/sendText",) and (record.get("json") or {}).get("message_id"):
                return web.json_response({"ok": True, "message_id": record["json"]["message_id"]})
            return web.json_response({"ok": True, "message_id": self.counter})
        result = handler(request, record) if callable(handler) else handler
        return result if isinstance(result, web.StreamResponse) else web.json_response(result)

    def calls(self, path):
        return [r for r in self.requests if r["path"] == path]

    def last(self, path):
        return self.calls(path)[-1]

    async def start(self):
        self.server = TestServer(self.app)
        await self.server.start_server()
        return str(self.server.make_url("/bot/v1"))

    async def close(self):
        if self.server is not None:
            await self.server.close()

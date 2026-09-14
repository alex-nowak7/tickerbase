"""Cloudflare Workers entrypoint. This is the only genuinely new file."""

from workers import WorkerEntrypoint
import asgi

import finnhub_client
import twelvedata_client
from server import app


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        # Secrets live on env, not os.environ.
        app.state.env = self.env
        finnhub_client.set_api_key(getattr(self.env, "FINNHUB_API_KEY", ""))
        twelvedata_client.set_api_key(getattr(self.env, "TWELVEDATA_API_KEY", ""))
        return await asgi.fetch(app, request.js_object, self.env)

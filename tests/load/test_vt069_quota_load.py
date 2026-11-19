import asyncio

import httpx
from tools.scripts import vt069_booking_quota_load


def test_synthetic_load_records_one_latency_for_invalid_json_response(monkeypatch):
    original_post = httpx.AsyncClient.post
    injected = False

    async def post_with_one_invalid_json(self, *args, **kwargs):
        nonlocal injected
        corrupt = not injected
        injected = True
        response = await original_post(self, *args, **kwargs)
        if corrupt:
            return httpx.Response(200, content=b"{", request=response.request)
        return response

    monkeypatch.setattr(httpx.AsyncClient, "post", post_with_one_invalid_json)
    report = asyncio.run(vt069_booking_quota_load.run())

    assert report["measured_requests"] == 60
    assert report["other_failures"] == 1
    assert report["business_correct_responses"] == 59

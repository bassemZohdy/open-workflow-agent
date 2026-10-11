import asyncio

import pytest

from openworkflow_adk.internal import BackpressureController


async def test_backpressure_hysteresis_blocks_until_low_watermark() -> None:
    controller = BackpressureController(max_inflight=1, high_watermark=3, low_watermark=1)
    controller.update_depth("api", 3)
    blocked = asyncio.create_task(controller.acquire("api"))
    await asyncio.sleep(0)
    assert not blocked.done()

    controller.update_depth("api", 1)
    await asyncio.wait_for(blocked, timeout=0.2)
    controller.release("api")


def test_backpressure_rejects_invalid_watermarks() -> None:
    with pytest.raises(ValueError):
        BackpressureController(high_watermark=1, low_watermark=1)

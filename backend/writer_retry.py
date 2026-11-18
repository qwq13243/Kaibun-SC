"""Bounded backoff for rate-limited story requests."""

from asyncio import sleep
from email.utils import parsedate_to_datetime
from random import uniform
from time import time


MAX_RETRIES = 2
MAX_DELAY = 30


def retry_delay(retries, retry_after):
    if retries >= MAX_RETRIES:
        return None
    delay = 5 * 2 ** retries + uniform(0, 1)
    if retry_after:
        try:
            if retry_after.isascii() and retry_after.isdecimal():
                provider_delay = int(retry_after)
            else:
                provider_delay = parsedate_to_datetime(retry_after).timestamp() - time()
            delay = max(delay, provider_delay)
        except (TypeError, ValueError, OverflowError, OSError):
            pass
    return delay if delay <= MAX_DELAY else None


async def wait_for_writer_retry(store, doc, error, retries):
    delay = retry_delay(retries, error.retry_after)
    if delay is None:
        raise error from None
    doc['phase'] = '模型服务请求受限，正在等待重试'
    store.save(doc)
    await sleep(delay)

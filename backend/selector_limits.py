"""Shared auxiliary-model request and conservative token reservations."""
import asyncio
import time
from collections import deque
from contextlib import asynccontextmanager


class SelectorLimits:
    def __init__(self, requests=0, tokens=0, *, clock=time.monotonic, sleep=asyncio.sleep):
        self.requests=max(0,int(requests))
        self.tokens=max(0,int(tokens))
        self.clock,self.sleep=clock,sleep
        self.entries=deque()
        self.gate=asyncio.Lock()

    @asynccontextmanager
    async def reserve(self, token_budget):
        budget=max(1,int(token_budget))
        if self.tokens and budget>self.tokens:
            raise ValueError('本次辅助模型输入及输出预算超过每分钟额度，请缩短本幕或提高配置额度；尚未发送请求')
        async with self.gate:
            while True:
                now=self.clock()
                while self.entries and now-self.entries[0][0]>=60:
                    self.entries.popleft()
                full_requests=self.requests and len(self.entries)>=self.requests
                full_tokens=self.tokens and sum(n for _,n in self.entries)+budget>self.tokens
                if not full_requests and not full_tokens:
                    entry=[now,budget]
                    self.entries.append(entry)
                    break
                await self.sleep(min(30,max(.01,60-(now-self.entries[0][0]))))
            yield entry

    def settle(self, entry, actual_tokens):
        if actual_tokens is not None:
            entry[1]=max(0,int(actual_tokens))
            entry[0]=self.clock()


def estimate_budget(settings, system, user, images=()):
    # UTF-8 byte count is deliberately conservative for text. Images reserve a
    # separate allowance because their tokens depend on provider preprocessing.
    # This is a queue reservation, not a model output cap or request parameter.
    return len((system+user).encode('utf-8'))+(settings.max_tokens or 16384)+8192*len(images)

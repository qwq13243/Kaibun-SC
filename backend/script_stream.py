"""Incremental JSON records; no record is committed before it is complete."""
import json


class ScriptStreamError(ValueError):
    pass


class JSONRecords:
    def __init__(self):
        self.buffer = ''
        self.scan = 0
        self.depth = 0
        self.quoted = False
        self.escaped = False

    def feed(self, chunk):
        self.buffer += chunk
        if len(self.buffer) > 2_000_000:
            raise ScriptStreamError('单条剧本记录过长，请检查模型输出格式')
        while self.scan < len(self.buffer):
            ch = self.buffer[self.scan]
            if self.depth == 0:
                if ch.isspace() or ch == '\ufeff':
                    self.scan += 1
                    continue
                if ch != '{':
                    raise ScriptStreamError('请仅输出逐条JSON对象，不输出Markdown或解释')
                # Discard whitespace preceding each record.
                self.buffer = self.buffer[self.scan:]
                self.scan = 0
                self.depth = 1
            elif self.quoted:
                if self.escaped:
                    self.escaped = False
                elif ch == '\\':
                    self.escaped = True
                elif ch == '"':
                    self.quoted = False
            elif ch == '"':
                self.quoted = True
            elif ch in '{[':
                self.depth += 1
            elif ch in '}]':
                self.depth -= 1
                if self.depth == 0:
                    try:
                        value = json.loads(self.buffer[:self.scan + 1])
                    except ValueError:
                        raise ScriptStreamError('剧本JSON记录格式有误') from None
                    self.buffer = self.buffer[self.scan + 1:]
                    self.scan = 0
                    yield value
                    continue
            self.scan += 1

    @property
    def incomplete(self):
        return bool(self.buffer.strip())


async def model_records(adapter, system, payload):
    """Close the HTTP stream on pause/error, including early validation failure."""
    decoder = JSONRecords()
    stream = adapter.stream_complete(system, json.dumps(payload, ensure_ascii=False))
    try:
        async for chunk in stream:
            for record in decoder.feed(chunk):
                yield record
        if decoder.incomplete:
            raise ScriptStreamError('输出在一条记录中间结束，完整内容已保存，继续会从保存处接写')
    finally:
        await stream.aclose()

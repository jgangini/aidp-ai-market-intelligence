"""Bounded real-fragment batching through the observed AIDP output_text shape."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from ai_market_intelligence.installation.stream import MarketGatewayStream
from ai_market_intelligence.runtime.workflow import _PublicTextGeneration
from ai_market_intelligence.runtime.wire import market_wire_line


def frame(text):
    event = {"type": "response.created", "response": {"output": [{"role": "assistant",
             "content": [{"type": "output_text", "text": text}]}]}}
    return b'data: ' + json.dumps(event, ensure_ascii=False).encode() + b'\n\n'


@pytest.mark.parametrize("source_characters,final_characters", [(3000, 4000), (20000, 20000)])
def test_multisource_text_fits_partial_messages_and_one_cumulative_final(
    source_characters, final_characters
):
    async def generate():
        raw = []
        owner = SimpleNamespace(_public_text_packets=0, _public_texts={},
                                current=lambda: {'request_id': 'volume', 'blocks': {}})
        for source in ('web', 'x', 'facebook', 'instagram', 'youtube', None):
            block = 'source-' + source if source else 'consolidated'
            sources = [source] if source else ['web', 'x', 'facebook', 'instagram', 'youtube']
            stream = _PublicTextGeneration(owner, block, source, sources, raw.append)
            owner._public_texts[block] = stream
            count = source_characters if source else final_characters
            text = ('Información pública ' * (count // 20 + 2))[:count]
            for offset in range(0, len(text), 20):
                stream.feed(text[offset:offset + 20])
            stream.end()
            stream.discard()
        assert len(raw) <= 1024
        return [market_wire_line('text', **p['_public_text']) for p in raw]

    lines = asyncio.run(generate())
    lines.append(market_wire_line('response', request_id='volume', response={
        'status': 'partial', 'markdown': 'Volumen controlado', 'references': [], 'artifacts': []}))
    transport = MarketGatewayStream()
    decoded = []
    for line in lines:
        decoded.extend(transport.feed(frame(line)))
    # The native wrapper observed in TEST repeats the concatenated transcript at closure.
    assert transport.feed(frame(''.join(lines))) == []
    decoded.extend(transport.feed(b'data: [DONE]\n\n'))
    transport.finish()
    assert len(decoded) == len(lines) and decoded[-1]['kind'] == 'response'
    assert transport.total_bytes < 32 * 1024 * 1024 and transport.frames <= 4096

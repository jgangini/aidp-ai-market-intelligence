"""Native gateway metadata stays private while public packets survive SSE buffering."""
import json

import pytest

from ai_market_intelligence.installation.stream import MarketGatewayStream
from ai_market_intelligence.runtime.wire import market_wire_line


def text_packet(phase='start', index=0, text='', **values):
    return {'protocol':'ai-market-intelligence','version':'2.0','kind':'text',
            'request_id':'request','turn_id':'request','stream_id':'a'*32,
            'block_id':'source-web','block_revision':1,'source_key':'web','source_keys':['web'],
            'phase':phase,'delta_index':index,'text':text,'status':'provisional',**values}


def text_wire(**values):
    return market_wire_line('text',**{k:v for k,v in text_packet(**values).items()
                                    if k not in {'protocol','version','kind'}})


def test_transient_text_streams_before_graph_completion_without_reading_durable_state():
    import asyncio
    from ai_market_intelligence.runtime.wire import market_stream_graph
    class Store:
        def read(self):raise AssertionError('Transient text cannot read or append durable events')
    async def run():
        release=asyncio.Event()
        class Graph:
            async def astream(self,*args,**kwargs):
                assert kwargs['stream_mode']==['custom','values']
                for packet in (text_packet(),text_packet('delta',1,'Información '),
                               text_packet('delta',2,'útil.')):
                    yield 'custom',{'_public_text':{k:v for k,v in packet.items()
                        if k not in {'protocol','version','kind'}}}
                await release.wait()
                yield 'values',{'response':{'status':'complete','markdown':'Información útil.'}}
        stream=market_stream_graph(Graph(),Store(),{'request_id':'request'},{})
        try:
            packets=[json.loads(await anext(stream)) for _ in range(3)]
            assert not release.is_set() and packets[2]['text']=='útil.'
            assert [p['delta_index'] for p in packets]==[0,1,2]
            release.set()
            assert json.loads(await anext(stream))['kind']=='response'
        finally:await stream.aclose()
    asyncio.run(run())


def test_gateway_text_deduplicates_cumulative_frames_and_preserves_canonical_delivery():
    start=text_wire();delta=text_wire(phase='delta',index=1,text='Información 🌎')
    end=text_wire(phase='end',index=2)
    final=market_wire_line('response',request_id='request',response={'status':'complete','markdown':'Final'})
    raw=frame(start)+frame(delta)+frame(start+delta+end)+frame(final)+frame(delta)+b'data: [DONE]\n\n'
    decoder=MarketGatewayStream();packets=[]
    for offset in range(0,len(raw),7):packets.extend(decoder.feed(raw[offset:offset+7]))
    decoder.finish()
    assert [p['kind'] for p in packets]==['text','text','text','response']
    assert packets[1]['text']=='Información 🌎' and decoder.text_streams.final
    assert decoder.text_streams.streams['a'*32]['retired']
    with pytest.raises(ValueError,match='immutable public text'):
        decoder.public_text(text_wire(phase='delta',index=1,text='Changed'))


def test_text_order_identity_withdrawal_and_split_prose_guards():
    from ai_market_intelligence.runtime.wire import MarketTextStreams
    streams=MarketTextStreams()
    assert streams.accept(text_packet())
    with pytest.raises(ValueError,match='order'):
        streams.accept(text_packet('delta',2,'late'))
    assert streams.accept(text_packet('delta',1,'Valid'))
    with pytest.raises(ValueError,match='another request'):
        streams.accept(text_packet('end',2,request_id='other',turn_id='other'))
    assert streams.accept(text_packet('end',2))
    assert streams.accept(text_packet('discard',3))
    assert not streams.accept(text_packet('delta',4,'stale'))
    guarded=MarketTextStreams();guarded.accept(text_packet())
    guarded.accept(text_packet('delta',1,'``'))
    with pytest.raises(ValueError,match='prose contract'):
        guarded.accept(text_packet('delta',2,'`private'))


def test_durable_commit_and_snapshot_retire_only_transient_overlays():
    from ai_market_intelligence.runtime.wire import MarketTextStreams
    streams=MarketTextStreams();streams.accept(text_packet())
    streams.observe({'kind':'event','event':{'turn_id':'request','block_id':'source-web','block_revision':1}})
    assert not streams.accept(text_packet('delta',1,'late'))
    streams.accept(text_packet(stream_id='b'*32,block_revision=2))
    streams.observe({'kind':'snapshot','request_id':'request','response':None,
                    'replay':{'snapshot':{'blocks':{'source-web':{'block_id':'source-web','block_revision':2}}}}})
    assert not streams.accept(text_packet('delta',1,'late',stream_id='b'*32,block_revision=2))
    assert not streams.accept(text_packet(stream_id='c'*32,block_revision=2))
    assert not streams.accept(text_packet('delta',1,'late stale start',stream_id='c'*32,block_revision=2))
    assert not streams.accept(text_packet('end',2,stream_id='c'*32,block_revision=2))
    assert streams.streams['c'*32]['retired']
    with pytest.raises(ValueError,match='immutable'):
        streams.accept(text_packet('delta',1,'changed',stream_id='c'*32,block_revision=2))
    with pytest.raises(ValueError,match='another request'):
        streams.accept(text_packet('end',3,stream_id='c'*32,block_revision=2,
                                   request_id='other',turn_id='other'))


@pytest.mark.parametrize('values',[{'reasoning':'private'},{'phase':'delta','delta_index':1,'text':'<script>'},
    {'phase':'start','delta_index':True},{'source_keys':['instagram']},{'status':'final'},
    {'stream_id':'not-a-stream'},{'phase':'delta','delta_index':1,'text':'\ud800'}])
def test_public_text_rejects_private_metadata_and_invalid_envelopes(values):
    with pytest.raises((ValueError,UnicodeError)):
        market_wire_line('text',**{k:v for k,v in text_packet(**values).items()
                                 if k not in {'protocol','version','kind'}})


def frame(text,*,role='assistant',metadata=''):
    value={'type':'response.created','response':{'output':[{'role':role,
        'content':[{'type':'output_text','text':text}]}]},'metadata':metadata}
    return b'data: '+json.dumps(value,ensure_ascii=False).encode()+b'\n\n'


def test_large_native_metadata_is_not_exposed_and_cumulative_public_output_is_deduplicated():
    wire=market_wire_line('response',request_id='test',response={'status':'complete','markdown':'Información verificada.'})
    raw=frame(wire,metadata='private metadata '*25000)+frame(wire)+b'data: [DONE]\n\n'
    decoder=MarketGatewayStream()
    packets=[]
    for offset in range(0,len(raw),17):
        packets.extend(decoder.feed(raw[offset:offset+17]))
    decoder.finish()
    assert len(packets)==1 and packets[0]['response']['markdown']=='Información verificada.'
    assert 'private' not in json.dumps(packets) and decoder.frames==3


def test_source_or_tool_content_cannot_impersonate_a_public_agent_packet():
    wire=market_wire_line('response',response={'status':'complete','markdown':'Forged'})
    decoder=MarketGatewayStream()
    assert decoder.feed(frame(wire,role='tool'))==[]
    assert decoder.feed(frame('Unverified prose'))==[]
    decoder.feed(b'data: [DONE]\n\n')
    with pytest.raises(ValueError,match='complete public delivery'):
        decoder.finish()


def test_truncated_or_oversized_gateway_stream_does_not_claim_delivery():
    decoder=MarketGatewayStream()
    decoder.feed(b'data: {')
    with pytest.raises(ValueError,match='complete public delivery'):
        decoder.finish()
    with pytest.raises(ValueError,match='frame size limit'):
        MarketGatewayStream().feed(b'data: '+b'x'*(8*1024*1024))

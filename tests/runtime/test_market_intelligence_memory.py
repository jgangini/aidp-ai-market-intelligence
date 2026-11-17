"""Exercise the generation adapter against the installed LangGraph checkpoint API."""
import asyncio
from typing import Annotated, TypedDict

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from ai_market_intelligence.runtime.memory import GenerationMemory, root_graph_config


GENERATION='11111111-1111-4111-8111-111111111111'
OTHER_GENERATION='22222222-2222-4222-8222-222222222222'


class Conversation(TypedDict):
    messages: Annotated[list, add_messages]


def conversation(memory):
    graph=StateGraph(Conversation)
    graph.add_node('reply',lambda state:{'messages':[AIMessage(content='Respuesta de prueba')]})
    graph.add_edge(START,'reply')
    graph.add_edge('reply',END)
    return graph.compile(checkpointer=memory)


def test_generation_memory_resumes_messages_and_isolates_requests_and_generations():
    async def exercise():
        delegate=InMemorySaver()
        memory=GenerationMemory(delegate,GENERATION,request_id='first')
        graph=conversation(memory)
        original={'configurable':{'thread_id':'authenticated-session','checkpoint_ns':'default'},
                  'metadata':{'checkpoint_ns':'default'}}
        config=root_graph_config(original)
        first=await graph.ainvoke({'messages':[HumanMessage(content='Primera pregunta')]},config)
        second=await graph.ainvoke({'messages':[HumanMessage(content='Segunda pregunta')]},config)
        assert len(first['messages'])==2 and len(second['messages'])==4
        saved=await graph.aget_state(config)
        assert [row.content for row in saved.values['messages']]==[row.content for row in second['messages']]
        assert isinstance(saved.values['messages'][0],HumanMessage)
        assert isinstance(saved.values['messages'][-1],AIMessage)
        history=[row async for row in graph.aget_state_history(config)]
        assert len(history)>=3 and all(row.config['configurable']['checkpoint_ns']=='' for row in history)
        assert original['configurable']['checkpoint_ns']==original['metadata']['checkpoint_ns']=='default'
        for generation,request in ((GENERATION,'second'),(OTHER_GENERATION,'first')):
            isolated=GenerationMemory(delegate,generation,request_id=request)
            assert (await conversation(isolated).aget_state(config)).values=={}
            raw=await delegate.aget_tuple(memory.namespaced(config))
            with pytest.raises(RuntimeError,match='another generation'):
                isolated.restored_tuple(raw)
        assert saved.config['configurable']['checkpoint_ns']==''
    asyncio.run(exercise())


def test_root_agent_rejects_external_subgraph_namespace():
    with pytest.raises(ValueError,match='external subgraph routes'):
        root_graph_config({'configurable':{'thread_id':'authenticated-session','checkpoint_ns':'foreign-subgraph'}})

import pytest
from anton.core.llm.provider import LLMResponse, LLMProvider, StreamEvent, TokenLimitExceeded
from cowork.services.failover_provider import FailoverLLMProvider, Candidate, AllCandidatesFailedError
from collections.abc import AsyncIterator

class DummyProvider(LLMProvider):
    name = "dummy"
    
    def __init__(self, responses, native=False):
        self._responses = responses
        self._native = native
        self.calls = 0

    async def complete(self, **kwargs) -> LLMResponse:
        resp = self._responses[self.calls]
        self.calls += 1
        if isinstance(resp, Exception):
            raise resp
        return resp

    async def stream(self, **kwargs) -> AsyncIterator[StreamEvent]:
        resp = self._responses[self.calls]
        self.calls += 1
        if isinstance(resp, Exception):
            raise resp
        yield StreamEvent(type="text", text=resp.text)

    def native_web_tools(self) -> set[str]:
        return {"search"} if self._native else set()

@pytest.mark.asyncio
async def test_failover_empty_completion():
    p1 = DummyProvider([LLMResponse(content="", tool_calls=[])])
    p2 = DummyProvider([LLMResponse(content="hello", tool_calls=[])])
    
    candidates = [
        Candidate(provider=p1, model="m1", label="p1/m1"),
        Candidate(provider=p2, model="m2", label="p2/m2"),
    ]
    failover = FailoverLLMProvider(candidates)
    
    res = await failover.complete(model="ignored", system="", messages=[])
    assert res.content == "hello"
    assert p1.calls == 1
    assert p2.calls == 1
    assert failover.last_served_by == "p2/m2"

@pytest.mark.asyncio
async def test_failover_all_empty():
    p1 = DummyProvider([LLMResponse(content="", tool_calls=[])])
    p2 = DummyProvider([LLMResponse(content="", tool_calls=[])])
    
    candidates = [
        Candidate(provider=p1, model="m1", label="p1/m1"),
        Candidate(provider=p2, model="m2", label="p2/m2"),
    ]
    failover = FailoverLLMProvider(candidates)
    
    res = await failover.complete(model="ignored", system="", messages=[])
    assert res.content == ""
    assert p1.calls == 1
    assert p2.calls == 1
    assert failover.last_served_by == "p2/m2"

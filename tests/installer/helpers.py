"""Frozen transport fixtures narrowed to the standalone installer's contract."""
import json
import threading
from types import SimpleNamespace
from ai_market_intelligence.transport import AidpClient


class FakeResponse:
    def __init__(self, body=None, *, headers=None, status_code=200, content=None):
        self._body = body
        self.headers = headers or {}
        self.status_code = status_code
        self.content = content if content is not None else (json.dumps(body).encode() if body is not None else b'')

    def json(self):
        return self._body


def bare_client():
    client = object.__new__(AidpClient)
    client.base = 'https://aidp.example.invalid/20260430/aiDataPlatforms/platform'
    client.signer = object()
    client._session_lock = threading.Lock()
    client._locks = {}
    client.settings = SimpleNamespace(objectstorage_namespace='namespace', aidp_region='us-chicago-1',
        agent_model_id='ocid1.generativeaimodel.oc1.us-chicago-1.example', compartment_id='ocid1.compartment.oc1..example')
    return client

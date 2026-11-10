"""Content-carried NDJSON envelopes survive gateways which omit message metadata."""

import asyncio
from html import escape
import hashlib
import json
import re
from urllib.parse import quote

from .research_contracts import MarketEvent
from .content_items import validate_content_items


Market_WIRE_LIMIT = 2 * 1024 * 1024
Market_TEXT_PACKET_LIMIT = 1024
Market_TEXT_STREAM_LIMIT = 20000
Market_TEXT_DELTA_LIMIT = 2048
_TEXT_FIELDS = frozenset({
    "protocol", "version", "kind", "request_id", "turn_id", "stream_id", "block_id",
    "block_revision", "source_key", "source_keys", "phase", "delta_index", "text", "status",
})
_TEXT_SOURCES = frozenset({
    "web", "x", "facebook", "instagram", "youtube", "market_data", "world_bank", "financial_document",
})


def _text_identity(packet):
    request = packet["request_id"]
    if not isinstance(request, str) or not 1 <= len(request) <= 128 or request != packet["turn_id"]:
        raise ValueError("Public text belongs to another request")
    _text_position(packet)


def _text_position(packet):
    stream_id = packet["stream_id"]
    if not isinstance(stream_id, str) or not re.fullmatch(r"[a-f0-9]{32}", stream_id):
        raise ValueError("Invalid public text stream identity")
    revision, index = packet["block_revision"], packet["delta_index"]
    if type(revision) is not int or revision < 1:
        raise ValueError("Invalid public text revision or index")
    if type(index) is not int or not 0 <= index < Market_TEXT_PACKET_LIMIT:
        raise ValueError("Invalid public text revision or index")


def _text_sources(sources):
    if not isinstance(sources, list) or len(sources) > 8:
        raise ValueError("Invalid public text source attribution")
    if any(not isinstance(item, str) or item not in _TEXT_SOURCES for item in sources):
        raise ValueError("Invalid public text source attribution")
    if len(sources) != len(set(sources)):
        raise ValueError("Invalid public text source attribution")


def _text_attribution(packet):
    source, sources = packet["source_key"], packet["source_keys"]
    _text_sources(sources)
    if source is None:
        valid = packet["block_id"] == "consolidated"
    else:
        valid = (isinstance(source, str) and source in _TEXT_SOURCES
                 and sources == [source] and packet["block_id"] == "source-" + source)
    if not valid:
        raise ValueError("Invalid public text block attribution")


def _text_fragment(packet):
    phase, text = packet["phase"], packet["text"]
    if phase not in ("start", "delta", "end", "discard") or packet["status"] != "provisional" or not isinstance(text, str):
        raise ValueError("Invalid public text phase")
    if phase == "delta":
        _text_prose_delta(text)
    elif text or (phase == "start" and packet["delta_index"] != 0):
        raise ValueError("Control text packets cannot carry prose")
    text.encode("utf-8")


def _text_prose_delta(text):
    if not 1 <= len(text) <= Market_TEXT_DELTA_LIMIT or "<" in text or "```" in text:
        raise ValueError("Invalid public prose delta")


def validate_text_packet(packet):
    expected = {"protocol": "ai-market-intelligence", "version": "2.0", "kind": "text"}
    if set(packet) != _TEXT_FIELDS or any(packet.get(key) != value for key, value in expected.items()):
        raise ValueError("Invalid public text envelope")
    _text_identity(packet)
    _text_attribution(packet)
    _text_fragment(packet)
    return packet


class MarketTextStreams:
    """Validate transient streams independently of the durable event cursor."""

    def __init__(self):
        self.request_id = None
        self.streams = {}
        self.seen = {}
        self.revisions = {}
        self.final = False

    def bind_request(self, request):
        if self.request_id is not None and request != self.request_id:
            raise ValueError("Public packets belong to another request")
        self.request_id = request

    def accept(self, packet):
        validate_text_packet(packet)
        if self.request_id not in (None, packet["request_id"]):
            raise ValueError("Public text belongs to another request")
        identity = (packet["stream_id"], packet["delta_index"])
        signature = hashlib.sha256(json.dumps(packet, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        prior = self.seen.get(identity)
        if prior is not None:
            if prior != signature:
                raise ValueError("An immutable public text packet changed")
            return False
        if len(self.seen) >= Market_TEXT_PACKET_LIMIT:
            raise ValueError("Public text exceeds its packet budget")
        stream = self.streams.get(packet["stream_id"])
        fresh = False
        if not self.final and not (stream and stream["retired"]):
            if packet["phase"] == "start":
                fresh = self.start(packet, stream)
            else:
                self.advance(packet, stream)
                fresh = True
        self.request_id = packet["request_id"]
        self.seen[identity] = signature
        return fresh

    def advance(self, packet, stream):
        if stream is None:
            raise ValueError("Public text delta arrived before its start")
        for field in ("block_id", "block_revision", "source_key", "source_keys"):
            if packet[field] != stream[field]:
                raise ValueError("Public text stream metadata changed")
        if packet["delta_index"] != stream["index"] + 1:
            raise ValueError("Public text packet order changed")
        if stream["ended"] and packet["phase"] != "discard":
            raise ValueError("Public text continued after generation ended")
        if packet["phase"] == "delta":
            self.append_text(packet["text"], stream)
        stream["index"] = packet["delta_index"]
        stream["ended"] = packet["phase"] in {"end", "discard"}
        stream["retired"] = packet["phase"] == "discard"

    @staticmethod
    def append_text(text, stream):
        length = stream["length"] + len(text)
        if length > Market_TEXT_STREAM_LIMIT or "```" in stream["tail"] + text:
            raise ValueError("Public text exceeds its prose contract")
        stream.update(length=length, tail=(stream["tail"] + text)[-2:])

    def start(self, packet, previous):
        if previous is not None:
            raise ValueError("Public text stream restarted")
        block = packet["block_id"]
        retired = packet["block_revision"] <= self.revisions.get(block, 0)
        if not retired:
            self.retire(block)
        self.streams[packet["stream_id"]] = {
            **{field: packet[field] for field in ("block_id", "block_revision", "source_key", "source_keys")},
            "index": 0, "length": 0, "tail": "", "ended": False, "retired": retired,
        }
        return not retired

    def retire(self, block=None, revision=float("inf")):
        for stream in self.streams.values():
            if (block is None or stream["block_id"] == block) and stream["block_revision"] <= revision:
                stream["retired"] = True

    def observe_block(self, event):
        block = event.get("block_id")
        if block:
            revision = event.get("block_revision", 0)
            self.revisions[block] = max(self.revisions.get(block, 0), revision)
            self.retire(block, revision)

    def recover(self, packet):
        self.retire()
        replay = packet.get("replay") or {}
        blocks = (replay.get("snapshot") or {}).get("blocks", {}).values()
        for event in [*blocks, *replay.get("events", [])]:
            self.observe_block(event)
        response = packet.get("response") or {}
        if response and response.get("status") != "pending" and not response.get("resumable"):
            self.final = True

    def observe(self, packet):
        request = packet.get("request_id") or (packet.get("event") or {}).get("turn_id")
        if request:
            self.bind_request(request)
        if packet["kind"] == "event":
            self.observe_block(packet["event"])
        elif packet["kind"] in {"snapshot", "response"}:
            self.recover(packet)


def market_chat_cell(value):
    text = escape(str(value), quote=False).replace("\n", " ").replace("\r", " ")
    for character in "\\`*_{}[]()#+.!|":
        text = text.replace(character, "\\" + character)
    return text


def _market_chat_fragment(packet):
    validate_text_packet(packet)
    if packet["phase"] == "delta":
        return packet["text"]
    if packet["phase"] == "start":
        source = market_chat_cell(packet["source_key"] or "informe")
        return f"\n\n**Avance de {source} — pendiente de revisión**\n\n"
    if packet["phase"] == "discard":
        return "\n\n**Avance retirado: se sustituirá por el contenido verificado.**\n\n"
    return None


def _market_chat_event(packet):
    event = packet["event"]
    if event["type"] == "source.coverage":
        source = market_chat_cell(event["source_key"])
        return f"\n\n**{source}**: {market_chat_cell(event['text'])}\n\n"
    if event["type"] not in {"draft.upsert", "draft.corrected"}:
        return None
    sources = event.get("source_keys") or []
    source = market_chat_cell(event["source_key"] or ", ".join(sources) or "informe")
    heading = "Avance de " + source + " — sujeto a revisión final"
    if event["type"] == "draft.corrected":
        heading = "Corrección de " + source + " — sustituye el avance anterior"
    if event["status"] == "failed":
        heading = "Retirada de " + source + " — el avance anterior no forma parte de la conclusión"
    return market_chat_report({
        "markdown": "**" + heading + "**\n\n" + event["text"],
        "references": packet.get("references", []),
        "limitations": event.get("limitations", []), "artifacts": [],
    })


def market_chat_text(packet):
    """Project public packets into an append-only chat; keep NDJSON intact for API clients."""
    if packet["kind"] == "heartbeat":
        return "\n"
    if packet["kind"] == "text":
        return _market_chat_fragment(packet)
    if packet["kind"] == "event":
        return _market_chat_event(packet)
    response = packet.get("response")
    return market_chat_report(response) if response else None


def market_chat_report(response):
    parts = [response["markdown"]]
    limits = [text for text in response.get("limitations", []) if text not in parts[0]]
    if limits:
        parts.append("**Limitaciones**\n\n" + "\n".join("- " + market_chat_cell(text) for text in limits))
    references = response.get("references", [])
    if references:
        links = []
        for number, row in enumerate(references, 1):
            url = quote(row["url"], safe=":/?=&%#@!$*+,;~._-()")
            links.append(f"- [{number}. {market_chat_cell(row['source'])}](<{url}>)")
        parts.append("**Fuentes**\n\n" + "\n".join(links))
    for artifact in response.get("artifacts", []):
        rows = artifact["rows"]
        columns = list(dict.fromkeys(key for row in rows for key in row
                                   if key != "evidence_id" and not key.endswith("_ids")))
        table = [" | ".join(market_chat_cell(key) for key in columns),
                 " | ".join("---" for _ in columns)]
        table.extend(" | ".join(market_chat_cell(row.get(key, "")) for key in columns)
                     for row in rows)
        parts.append("**" + market_chat_cell(artifact["title"]) + "**\n\n"
                     + market_chat_cell(artifact["x_label"]) + " / "
                     + market_chat_cell(artifact["y_label"]) + "; "
                     + market_chat_cell(artifact["unit"]) + " "
                     + market_chat_cell(artifact.get("currency") or "") + "\n\n"
                     + market_chat_cell(artifact["caption"]) + "\n\n"
                     + "\n".join(table))
    return "\n\n" + "\n\n".join(parts) + "\n"


def market_public_response(response):
    if response is None:
        return None
    return {key: value for key, value in response.items() if key != "content_items"}


def market_lift_content_manifest(packet):
    if packet["kind"] not in {"response", "snapshot"}:
        return
    if packet["kind"] == "response" and "content_items" in (packet.get("response") or {}):
        response = packet["response"]
        packet["content_items"] = response["content_items"]
        packet["references"] = response.get("references", [])
    if "response" in packet:
        packet["response"] = market_public_response(packet["response"])
    replay = packet.get("replay")
    snapshot = (replay or {}).get("snapshot")
    if snapshot and "response" in snapshot:
        packet["replay"] = {**replay, "snapshot": {
            **snapshot, "response": market_public_response(snapshot["response"]),
        }}


def market_wire_line(kind, **values):
    if kind not in {"event", "response", "snapshot", "heartbeat", "text"}:
        raise ValueError("Unknown public wire envelope")
    packet = {
        "protocol": "ai-market-intelligence",
        "version": "2.0",
        "kind": kind,
        **values,
    }
    if kind == "text":
        validate_text_packet(packet)
    market_lift_content_manifest(packet)
    market_validate_content_manifest(packet)
    text = (
        json.dumps(packet, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        + "\n"
    )
    if len(text.encode("utf-8")) > Market_WIRE_LIMIT:
        raise ValueError("Public wire packet exceeds its size bound")
    return text


def market_reference_manifest(evidence, identities):
    fields = (
        "evidence_id",
        "source",
        "discovery_channel",
        "url",
        "requested_url",
        "author_url",
        "author_name",
        "source_title",
        "source_relation",
        "media",
        "media_omitted_count",
        "media_content_analyzed",
        "published_at",
        "data_last_updated",
        "observed_at",
        "content_basis",
        "locator",
        "document_sha256",
    )
    return [
        {key: evidence[identity][key] for key in fields if key in evidence[identity]}
        for identity in identities
    ]


def market_content_manifest(value, response=None):
    if response is not None and "content_items" in response:
        collection = response["content_items"]
    elif value is not None and "content_items" in value:
        collection = value["content_items"]
    else:
        return {}
    return {"content_items": validate_content_items(collection, value["evidence"])}


def market_content_evidence_ids(collection):
    identities = set()
    for item in collection.get("items", []):
        identities.update(item["evidence_ids"])
        identities.update((item.get("comments_analysis") or {}).get("evidence_ids", []))
    return identities


def market_validate_content_manifest(packet):
    collection = packet.get("content_items")
    if isinstance(collection, dict) and collection.get("schema_version") == "1.0":
        references = packet.get("references", [])
        validate_content_items(collection, references)


def market_event_packet(store, event, *, publication=None):
    event = MarketEvent.model_validate(event).model_dump(mode="json")
    fingerprint = hashlib.sha256(json.dumps(event, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if publication is not None and event["event_id"] in publication["events"]:
        if publication["events"][event["event_id"]] != fingerprint:
            raise ValueError("An immutable public event changed")
        return None
    value, _ = store.read()
    content = market_content_manifest(value)
    revision = content.get("content_items", {}).get("revision")
    if publication is not None and revision == publication.get("content_revision"):
        content = {}
    extra = market_content_evidence_ids(content.get("content_items", {})) - set(event["evidence_ids"])
    references = market_reference_manifest(value["evidence"], [*event["evidence_ids"], *sorted(extra)])
    artifacts = [value["artifacts"][identity] for identity in event["artifact_ids"]]
    line = market_wire_line(
        "event", event=event, references=references, artifacts=artifacts, **content
    )
    if publication is not None:
        publication["events"][event["event_id"]] = fingerprint
        publication["content_revision"] = revision
    return line


def market_visible_artifacts(value):
    if value is None:
        return []
    response = value["response"]
    if response and response["status"] != "pending" and not response.get("resumable"):
        return response.get("artifacts", [])
    identities = {
        identity
        for block in value["blocks"].values()
        if block["type"] != "draft.compacted" and block["status"] != "failed"
        for identity in block["evidence_ids"]
    }
    return [
        artifact
        for artifact in value["artifacts"].values()
        if set(artifact["evidence_ids"]).issubset(identities)
    ]


def market_replay_packet(store, request_id, after_sequence):
    value, replay = store.replay_snapshot(after_sequence)
    blocks = (replay.get("snapshot") or {}).get("blocks", {}).values()
    artifacts = market_visible_artifacts(value)
    if replay["snapshot"] is not None:
        replay["snapshot"]["artifacts"] = {
            artifact["artifact_id"]: artifact for artifact in artifacts
        }
    response = value["response"] if value else None
    content = market_content_manifest(value, response)
    identities = {
        identity
        for event in [*replay["events"], *blocks]
        for identity in event["evidence_ids"]
    }
    identities.update(
        identity for artifact in artifacts for identity in artifact["evidence_ids"]
    )
    identities.update(
        row["evidence_id"] for row in (response or {}).get("references", [])
    )
    identities.update(market_content_evidence_ids(content.get("content_items", {})))
    references = (
        market_reference_manifest(value["evidence"], sorted(identities))
        if value
        else []
    )
    return market_wire_line(
        "snapshot",
        request_id=request_id,
        replay=replay,
        response=response,
        artifacts=artifacts,
        references=references,
        **content,
    )


def validate_wire_packet(packet):
    if (
        not isinstance(packet, dict)
        or packet.get("protocol") != "ai-market-intelligence"
        or packet.get("version") != "2.0"
        or packet.get("kind") not in {"event", "response", "snapshot", "heartbeat", "text"}
    ):
        raise ValueError("Unexpected native wire protocol")
    if packet["kind"] == "text":
        validate_text_packet(packet)
    else:
        market_validate_content_manifest(packet)


class MarketWireDecoder:
    def __init__(self):
        self.buffer = ""

    def feed(self, text):
        if not isinstance(text, str):
            raise ValueError("Wire decoding requires decoded text fragments")
        self.buffer += text
        packets = []
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            if not line:
                continue
            if len(line.encode("utf-8")) > Market_WIRE_LIMIT:
                raise ValueError("Public wire packet exceeds its size bound")
            packet = json.loads(line)
            validate_wire_packet(packet)
            packets.append(packet)
        if len(self.buffer.encode("utf-8")) > Market_WIRE_LIMIT:
            raise ValueError("Incomplete wire packet exceeds its size bound")
        return packets

    def finish(self):
        if self.buffer.strip():
            raise ValueError("The native stream ended inside a public packet")


def market_custom_packet(store, data, request_id, *, publication=None):
    if not isinstance(data, dict) or "_public_text" not in data:
        return market_event_packet(store, data, publication=publication)
    values = data["_public_text"]
    if not isinstance(values, dict) or values.get("request_id") != request_id:
        raise ValueError("Transient text belongs to another request")
    return market_wire_line("text", **values)


async def market_stream_graph(graph, store, request, config, *, heartbeat_seconds=10):
    stream = graph.astream(request, config=config, stream_mode=["custom", "values"])
    pending = None
    response = None
    publication = {"events": {}, "content_revision": None}
    try:
        while True:
            if pending is None:
                pending = asyncio.create_task(stream.__anext__())
            done, _ = await asyncio.wait({pending}, timeout=heartbeat_seconds)
            if not done:
                yield market_wire_line("heartbeat", request_id=request["request_id"])
                continue
            try:
                mode, data = pending.result()
            except StopAsyncIteration:
                break
            pending = None
            if mode == "custom":
                packet = market_custom_packet(store, data, request["request_id"], publication=publication)
                if packet is not None:
                    yield packet
            elif mode == "values" and data.get("response"):
                response = data["response"]
        if response is None:
            raise RuntimeError(
                "Research ended without a response or an explicit pending outcome"
            )
        yield market_wire_line(
            "response", request_id=request["request_id"], response=response
        )
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await stream.aclose()

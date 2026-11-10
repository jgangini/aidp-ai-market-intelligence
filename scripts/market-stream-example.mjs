// Consume normalized public packets from the backend MarketGatewayStream, never raw model/tool frames.
export class ReplayRequired extends Error {
  constructor(afterSequence) {
    super('Recover this request with replay before applying more events.');
    this.afterSequence = afterSequence;
  }
}

export function createMarketView(requestId) {
  if (typeof requestId !== 'string' || !requestId || requestId.length > 128) throw new TypeError('Invalid request identity');
  return { requestId, cursor: 0, blocks: new Map(), artifacts: new Map(), references: new Map(),
    events: new Map(), activity: new Map(), textStreams: new Map(), textBlocks: new Map(),
    textPackets: new Map(), contentItems: null, contentItemHistory: new Map(), response: null, final: null };
}

function canonical(value) {
  return JSON.stringify(value, (_key, item) => item && typeof item === 'object' && !Array.isArray(item)
    ? Object.fromEntries(Object.entries(item).sort(([a], [b]) => a.localeCompare(b))) : item);
}

function unsafeMediaHost(host) {
  return !host.includes('.') || /(?:^|\.)(?:localhost|local|localdomain|internal|onion|test|invalid)$/u.test(host)
    || host === 'home.arpa' || host.endsWith('.home.arpa') || /^[\d.]+$/u.test(host) || host.includes(':');
}

function validateMediaURL(value) {
  if (typeof value !== 'string' || value.length > 4096 || /[\s\\\u0000-\u001f\u007f]/u.test(value))
    throw new TypeError('Invalid media URL');
  const media = new URL(value);
  if (media.protocol !== 'https:' || media.username || media.password || media.port || media.hash
      || unsafeMediaHost(media.hostname.toLowerCase())) throw new TypeError('Unsafe media reference');
}

function validateMediaAsset(asset) {
  validateMediaURL(asset.url);
  if (!['image', 'video'].includes(asset.kind) || !['direct', 'page'].includes(asset.delivery)
      || !['attachment', 'thumbnail', 'preview'].includes(asset.role)) throw new TypeError('Unsafe media reference');
}

function validateMediaManifest(assets) {
  if (!Array.isArray(assets) || assets.length > 8) throw new TypeError('Invalid media manifest');
  for (const asset of assets) validateMediaAsset(asset);
}

function manifests(view, references = [], artifacts = []) {
  if (!Array.isArray(references) || !Array.isArray(artifacts) || references.length > 800 || artifacts.length > 800)
    throw new TypeError('Invalid public manifest');
  for (const reference of references) {
    const url = new URL(reference.url);
    if (url.protocol !== 'https:' || url.username || url.password || url.port) throw new TypeError('Unsafe source link');
    if (reference.media !== undefined) {
      validateMediaManifest(reference.media);
    }
    const prior = view.references.get(reference.evidence_id);
    if (prior && prior.url !== reference.url) throw new TypeError('Evidence identity changed destination');
    view.references.set(reference.evidence_id, { ...prior, ...reference });
  }
  for (const artifact of artifacts) view.artifacts.set(artifact.artifact_id, artifact);
}

function contentCheck(condition, message) {
  if (!condition) throw new TypeError('Invalid content items: ' + message);
}

function contentFields(value, required, optional = []) {
  contentCheck(value !== null && typeof value === 'object' && !Array.isArray(value), 'object required');
  const fields = new Set([...required, ...optional]);
  contentCheck(required.every(key => Object.hasOwn(value, key)), 'missing field');
  contentCheck(Object.keys(value).every(key => fields.has(key)), 'unknown field');
}

function contentString(value, maximum) {
  return typeof value === 'string' && [...value].length >= 1 && [...value].length <= maximum
    && !/[\ud800-\udfff]/u.test(value);
}

function contentStrings(values, maximumCount, maximumLength, minimumCount = 0) {
  contentCheck(Array.isArray(values) && values.length >= minimumCount && values.length <= maximumCount, 'list bound');
  contentCheck(values.every(value => contentString(value, maximumLength)), 'string bound');
}

function contentIDs(values, minimum = 0) {
  contentStrings(values, 50, 500, minimum);
  contentCheck(new Set(values).size === values.length, 'duplicate evidence identity');
}

function contentProse(value) {
  contentCheck(contentString(value, 3000) && Boolean(value.trim()), 'prose bound');
  contentCheck(!value.includes('<') && !value.includes('```'), 'HTML or code in prose');
  contentCheck(!/(?:\b(?:https?|javascript|data):|\[[^\]]*\]\s*\()/iu.test(value), 'URL in prose');
}

function contentURL(value) {
  contentCheck(contentString(value, 2000), 'source URL bound');
  const url = new URL(value);
  contentCheck(url.protocol === 'https:' && !url.username && !url.password && !url.port, 'source URL');
  url.hash = '';
  const parameters = [...url.searchParams].filter(([key]) => !/^utm_/iu.test(key) && !['fbclid', 'gclid'].includes(key.toLowerCase()));
  url.search = new URLSearchParams(parameters).toString();
  return url.href;
}

function contentReferences(ids, source, references, used) {
  return ids.map(identity => {
    const reference = references.get(identity);
    contentCheck(reference?.evidence_id === identity && reference.source === source, 'missing or foreign evidence');
    contentCheck(!used.has(identity), 'evidence belongs to multiple items');
    used.add(identity);
    return reference;
  });
}

function contentComments(comments, item, primary, references, used) {
  contentFields(comments, ['status', 'parent_item_id', 'evidence_ids', 'text', 'analyzed_count', 'limitations']);
  contentCheck(['not_collected', 'unavailable', 'partial', 'complete'].includes(comments.status), 'comment status');
  contentCheck(comments.parent_item_id === item.item_id, 'comment parent');
  contentIDs(comments.evidence_ids);
  contentStrings(comments.limitations, 12, 500);
  contentCheck(Number.isSafeInteger(comments.analyzed_count) && comments.analyzed_count >= 0
    && comments.analyzed_count <= comments.evidence_ids.length, 'comment count');
  if (['not_collected', 'unavailable'].includes(comments.status)) {
    contentCheck(comments.text === null && comments.analyzed_count === 0 && comments.evidence_ids.length === 0, 'unanalysed comments');
  } else {
    contentProse(comments.text);
    contentCheck(comments.analyzed_count > 0, 'analysed comments');
  }
  const rows = contentReferences(comments.evidence_ids, item.source_key, references, used);
  for (const row of rows) {
    contentCheck(row.content_basis === 'comment_text' && contentURL(row.requested_url) === contentURL(primary.url), 'comment evidence parent');
  }
}

function contentItem(item, references, used) {
  contentFields(item, ['item_id', 'item_revision', 'source_key', 'kind', 'primary_evidence_id',
    'evidence_ids', 'text', 'content_basis', 'limitations'], ['comments_analysis']);
  contentCheck(typeof item.item_id === 'string' && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/u.test(item.item_id), 'item identity');
  contentCheck(Number.isSafeInteger(item.item_revision) && item.item_revision > 0, 'item revision');
  contentCheck(contentString(item.source_key, 128), 'source identity');
  contentCheck(['video', 'post', 'article', 'page', 'document', 'record', 'unknown'].includes(item.kind), 'item kind');
  contentIDs(item.evidence_ids, 1);
  contentCheck(item.evidence_ids.includes(item.primary_evidence_id), 'primary evidence');
  contentProse(item.text);
  contentStrings(item.content_basis, 8, 128, 1);
  contentCheck(new Set(item.content_basis).size === item.content_basis.length, 'duplicate content basis');
  contentStrings(item.limitations, 12, 500);
  const rows = contentReferences(item.evidence_ids, item.source_key, references, used);
  const primary = references.get(item.primary_evidence_id);
  for (const row of rows) {
    contentCheck(row.content_basis !== 'comment_text' && contentURL(row.url) === contentURL(primary.url)
      && (row.locator ?? null) === (primary.locator ?? null), 'parent evidence unit');
  }
  const bases = [...new Set(rows.map(row => row.content_basis))].sort();
  contentCheck(canonical(bases) === canonical([...item.content_basis].sort()), 'evidence content basis');
  if (item.comments_analysis !== undefined && item.comments_analysis !== null)
    contentComments(item.comments_analysis, item, primary, references, used);
}

export function validateContentItems(collection, references) {
  if (collection?.schema_version !== '1.0') return false;
  contentFields(collection, ['schema_version', 'revision', 'items']);
  contentCheck(Number.isSafeInteger(collection.revision) && collection.revision > 0, 'collection revision');
  contentCheck(Array.isArray(collection.items) && collection.items.length <= 400, 'collection item bound');
  contentCheck(new TextEncoder().encode(JSON.stringify(collection)).byteLength <= 1024 * 1024, 'collection byte bound');
  const used = new Set();
  const identities = new Set();
  for (const item of collection.items) {
    contentItem(item, references, used);
    contentCheck(!identities.has(item.item_id), 'duplicate item identity');
    identities.add(item.item_id);
  }
  return true;
}

function contentCollection(view, collection) {
  if (!validateContentItems(collection, view.references)) return;
  const prior = view.contentItems;
  if (prior && collection.revision < prior.revision) return;
  if (prior && collection.revision === prior.revision) {
    contentCheck(canonical(collection) === canonical(prior), 'immutable collection changed');
    return;
  }
  for (const item of collection.items) {
    const previous = view.contentItemHistory.get(item.item_id);
    if (previous) {
      contentCheck(item.item_revision >= previous.revision, 'item revision regressed');
      if (item.item_revision === previous.revision)
        contentCheck(canonical(item) === previous.payload, 'immutable item changed');
    }
    view.contentItemHistory.set(item.item_id, { revision: item.item_revision, payload: canonical(item) });
  }
  view.contentItems = collection;
}

const sources = new Set(['web', 'x', 'facebook', 'instagram', 'youtube', 'market_data', 'world_bank', 'financial_document']);
const roles = new Set(['coordinator', 'market_analysis', 'financial_analysis', 'analysis_visualization']);
const textFields = ['protocol', 'version', 'kind', 'request_id', 'turn_id', 'stream_id', 'block_id',
  'block_revision', 'source_key', 'source_keys', 'phase', 'delta_index', 'text', 'status'].sort();

function validateText(packet) {
  if (canonical(Object.keys(packet).sort()) !== canonical(textFields)
      || typeof packet.request_id !== 'string' || !packet.request_id || packet.request_id.length > 128
      || packet.request_id !== packet.turn_id || typeof packet.stream_id !== 'string'
      || !/^[a-f0-9]{32}$/u.test(packet.stream_id)
      || !Number.isSafeInteger(packet.block_revision) || packet.block_revision < 1
      || !Number.isSafeInteger(packet.delta_index) || packet.delta_index < 0 || packet.delta_index >= 1024
      || !Array.isArray(packet.source_keys) || packet.source_keys.length > 8
      || packet.source_keys.some(source => !sources.has(source))
      || new Set(packet.source_keys).size !== packet.source_keys.length
      || !['start', 'delta', 'end', 'discard'].includes(packet.phase)
      || packet.status !== 'provisional' || typeof packet.text !== 'string')
    throw new TypeError('Invalid public text envelope');
  if (packet.source_key === null ? packet.block_id !== 'consolidated'
    : !sources.has(packet.source_key) || packet.block_id !== 'source-' + packet.source_key
      || packet.source_keys.length !== 1 || packet.source_keys[0] !== packet.source_key)
    throw new TypeError('Invalid public text attribution');
  if (packet.phase === 'delta' ? !packet.text || [...packet.text].length > 2048
      || packet.text.includes('<') || packet.text.includes('```') || /[\ud800-\udfff]/u.test(packet.text)
    : packet.text || packet.phase === 'start' && packet.delta_index !== 0)
    throw new TypeError('Invalid public text fragment');
}

function retireText(view, blockId = null, revision = Infinity) {
  for (const [id, stream] of view.textStreams) {
    if ((blockId === null || stream.block_id === blockId) && stream.block_revision <= revision) {
      view.textStreams.set(id, { ...stream, retired: true, text: '' });
      if (view.textBlocks.get(stream.block_id)?.stream_id === id) view.textBlocks.delete(stream.block_id);
    }
  }
}

function textFragment(view, packet) {
  validateText(packet);
  const identity = packet.stream_id + ':' + packet.delta_index;
  const signature = canonical(packet);
  const prior = view.textPackets.get(identity);
  if (prior !== undefined) {
    if (prior !== signature) throw new TypeError('An immutable public text packet changed');
    return;
  }
  if (view.textPackets.size >= 1024) throw new TypeError('Public text exceeds its packet budget');
  view.textPackets.set(identity, signature);
  const previous = view.textStreams.get(packet.stream_id);
  if (view.final || previous?.retired) return;
  if (packet.phase === 'start') {
    if (previous) throw new TypeError('Public text stream restarted');
    const committed = view.blocks.get(packet.block_id)?.block_revision ?? 0;
    const retired = packet.block_revision <= committed;
    if (!retired) retireText(view, packet.block_id);
    const stream = { ...packet, retired, ended: false, length: 0 };
    view.textStreams.set(packet.stream_id, stream);
    if (!retired) view.textBlocks.set(packet.block_id, stream);
    return;
  }
  if (!previous || packet.delta_index !== previous.delta_index + 1)
    throw new TypeError('Public text fragment is missing its start or arrived out of order');
  for (const field of ['block_id', 'block_revision', 'source_key', 'source_keys']) {
    if (canonical(packet[field]) !== canonical(previous[field])) throw new TypeError('Public text metadata changed');
  }
  if (previous.ended && packet.phase !== 'discard') throw new TypeError('Public text continued after its end');
  const text = previous.text + packet.text;
  const length = previous.length + [...packet.text].length;
  if (length > 20000 || text.includes('```')) throw new TypeError('Public text exceeds its prose contract');
  const stream = { ...previous, delta_index: packet.delta_index, text, length,
    phase: packet.phase, ended: ['end', 'discard'].includes(packet.phase), retired: packet.phase === 'discard' };
  view.textStreams.set(packet.stream_id, stream);
  if (stream.retired) {
    view.textStreams.set(packet.stream_id, { ...stream, text: '' });
    view.textBlocks.delete(packet.block_id);
  } else view.textBlocks.set(packet.block_id, stream);
}

function activity(view, item) {
  if (item.type !== 'source.coverage' || !item.coverage?.phase) return;
  if (!sources.has(item.source_key) || !roles.has(item.role_key) || item.coverage?.source !== item.source_key
      || !Array.isArray(item.source_keys) || item.source_keys.length !== 1 || item.source_keys[0] !== item.source_key)
    throw new TypeError('Invalid source activity attribution');
  view.activity.set(item.source_key, item);
}

function event(view, item) {
  if (item.turn_id !== view.requestId || !Number.isSafeInteger(item.sequence) || item.sequence < 1)
    throw new TypeError('Event belongs to another request or has an invalid sequence');
  const identity = canonical(item);
  const prior = view.events.get(item.event_id);
  if (prior !== undefined) {
    if (prior !== identity) throw new TypeError('An immutable event changed');
    return false;
  }
  if (item.sequence !== view.cursor + 1) throw new ReplayRequired(view.cursor);
  view.events.set(item.event_id, identity);
  view.cursor = item.sequence;
  activity(view, item);
  if (item.block_id) {
    view.blocks.set(item.block_id, item);
    retireText(view, item.block_id, item.block_revision ?? 0);
  }
  return true;
}

function finalResponse(view, response) {
  if (!response) return;
  retireText(view);
  view.response = response;
  if (response.status === 'pending' || response.resumable) return;
  view.final = response;
  view.artifacts = new Map();
  manifests(view, response.references, response.artifacts);
}

function snapshot(view, packet) {
  const replay = packet.replay;
  if (!replay || !Number.isSafeInteger(replay.cursor) || replay.cursor < view.cursor)
    throw new TypeError('Invalid replay cursor');
  retireText(view);
  if (replay.snapshot) {
    view.blocks = new Map();
    view.artifacts = new Map();
    view.activity = new Map();
    for (const block of Object.values(replay.snapshot.blocks)) {
      if (block.turn_id !== view.requestId) throw new TypeError('Foreign replay block');
      const prior = view.events.get(block.event_id);
      if (prior !== undefined && prior !== canonical(block)) throw new TypeError('An immutable event changed');
      view.events.set(block.event_id, canonical(block));
      view.blocks.set(block.block_id, block);
    }
    manifests(view, [], Object.values(replay.snapshot.artifacts));
    for (const item of Object.values(replay.snapshot.activity ?? {})) {
      if (item.turn_id !== view.requestId || !Number.isSafeInteger(item.sequence)
          || item.sequence < 1 || item.sequence > replay.cursor || item.type !== 'source.coverage')
        throw new TypeError('Invalid replay activity');
      const prior = view.events.get(item.event_id);
      if (prior !== undefined && prior !== canonical(item)) throw new TypeError('An immutable event changed');
      view.events.set(item.event_id, canonical(item));
      activity(view, item);
    }
    view.cursor = replay.cursor;
  } else {
    for (const item of replay.events) event(view, item);
    if (view.cursor !== replay.cursor) throw new ReplayRequired(view.cursor);
  }
  finalResponse(view, packet.response);
}

function hideWithdrawnArtifacts(view) {
  const retired = new Set([...view.blocks.values()]
    .filter(block => block.type === 'draft.corrected' && block.status === 'failed')
    .flatMap(block => block.evidence_ids));
  for (const [identity, artifact] of view.artifacts) {
    if (artifact.evidence_ids.some(id => retired.has(id))) view.artifacts.delete(identity);
  }
}

export function reduceMarketPacket(current, incoming) {
  const packet = structuredClone(incoming);
  if (packet?.protocol !== 'ai-market-intelligence' || packet.version !== '2.0')
    throw new TypeError('Expected a normalized public current packet');
  const turn = packet.kind === 'event' ? packet.event?.turn_id : packet.request_id;
  if (turn !== current.requestId) throw new TypeError('Packet belongs to another request');
  const view = { ...current, blocks: new Map(current.blocks), artifacts: new Map(current.artifacts),
    references: new Map(current.references), events: new Map(current.events), activity: new Map(current.activity),
    textStreams: new Map(current.textStreams), textBlocks: new Map(current.textBlocks), textPackets: new Map(current.textPackets),
    contentItemHistory: new Map(current.contentItemHistory) };
  const handlers = {
    heartbeat: () => {},
    text: () => textFragment(view, packet),
    event: () => { if (event(view, packet.event)) manifests(view, packet.references, packet.artifacts); },
    response: () => { manifests(view, packet.references, packet.artifacts); finalResponse(view, packet.response); },
    snapshot: () => { manifests(view, packet.references, packet.artifacts); snapshot(view, packet); },
  };
  if (!Object.hasOwn(handlers, packet.kind)) throw new TypeError('Unknown public packet kind');
  handlers[packet.kind]();
  if (Object.hasOwn(packet, 'content_items')) {
    if (current.final && packet.kind === 'event') validateContentItems(packet.content_items, view.references);
    else contentCollection(view, packet.content_items);
  }
  if (view.final) view.artifacts = new Map(view.final.artifacts.map(artifact => [artifact.artifact_id, artifact]));
  hideWithdrawnArtifacts(view);
  return view;
}

export async function* readMarketPackets(readable) {
  const reader = readable.getReader();
  const decoder = new TextDecoder('utf-8', { fatal: true });
  const encoder = new TextEncoder();
  const maximum = 2 * 1024 * 1024;
  let buffer = '';
  let done = false;
  try {
    while (!done) {
      const chunk = await reader.read();
      done = chunk.done;
      buffer += done ? decoder.decode() : decoder.decode(chunk.value, { stream: true });
      let newline;
      while ((newline = buffer.indexOf('\n')) !== -1) {
        const line = buffer.slice(0, newline);
        buffer = buffer.slice(newline + 1);
        if (!line.trim()) continue;
        if (encoder.encode(line).byteLength > maximum) throw new TypeError('Public packet exceeds its size bound');
        const packet = JSON.parse(line);
        if (packet?.protocol !== 'ai-market-intelligence' || packet.version !== '2.0'
            || !['event', 'response', 'snapshot', 'heartbeat', 'text'].includes(packet.kind))
          throw new TypeError('Expected a normalized public packet');
        if (packet.kind === 'text') validateText(packet);
        yield packet;
      }
      if (encoder.encode(buffer).byteLength > maximum) throw new TypeError('Incomplete packet exceeds its size bound');
    }
    if (buffer.trim()) throw new TypeError('Stream ended inside a public packet');
  } finally {
    try { if (!done) await reader.cancel(); } finally { reader.releaseLock(); }
  }
}

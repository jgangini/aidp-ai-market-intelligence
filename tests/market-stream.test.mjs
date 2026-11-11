import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createMarketView, reduceMarketPacket, ReplayRequired, readMarketPackets } from '../scripts/market-stream-example.mjs';

const envelope = values => ({ protocol: 'ai-market-intelligence', version: '2.0', ...values });
const event = (sequence, values = {}) => ({ event_id: `event-${sequence}`, sequence, turn_id: 'request',
  type: 'draft.upsert', block_id: 'source-facebook', status: 'provisional', role_key: 'market_analysis',
  source_key: 'facebook', source_keys: ['facebook'], evidence_ids: ['source'], text: 'Hallazgo atribuido.', ...values });
const reference = { evidence_id: 'source', url: 'https://example.org/public', source: 'facebook' };
const artifact = { artifact_id: 'plot', kind: 'line', evidence_ids: ['source'], rows: [] };
const contentFixture = JSON.parse(readFileSync(new URL('./fixtures/content-items-v1.json', import.meta.url), 'utf8'));
const presentationFixture = readFileSync(new URL('./fixtures/presentation-v1.ndjson', import.meta.url), 'utf8')
  .trim().split('\n').map(line => JSON.parse(line));
const contentPacket = (collection, sequence = 1, references = contentFixture.references) => envelope({ kind: 'event',
  event: event(sequence, { type: 'source.coverage', block_id: null, evidence_ids: [] }),
  references, artifacts: [], content_items: collection });
const text = (phase = 'start', index = 0, fragment = '', values = {}) => envelope({ kind: 'text',
  request_id: 'request', turn_id: 'request', stream_id: 'a'.repeat(32), block_id: 'source-web',
  block_revision: 1, source_key: 'web', source_keys: ['web'], phase, delta_index: index,
  text: fragment, status: 'provisional', ...values });

test('existing v2 consumer accepts presentation metadata without rewriting prose or partial status', () => {
  let view = createMarketView('fixture-request');
  for (const packet of presentationFixture) view = reduceMarketPacket(view, packet);
  assert.equal(view.final.status, 'partial');
  assert.equal(view.final.markdown, 'La muestra es parcial.');
  assert.equal(view.textBlocks.size, 0);
  assert.equal(view.activity.size, 1); // Existing view is still keyed by source.
  const snapshot = presentationFixture.find(packet => packet.kind === 'snapshot');
  assert.equal(Object.keys(snapshot.replay.snapshot.activities).length, 2);
  assert.equal(view.activity.get('facebook').coverage.presentation.schema_version, '1.0');
});

test('true text fragments are visible before completion without changing the durable cursor', async () => {
  let controller;
  const body = new ReadableStream({ start(value) { controller = value; } });
  const iterator = readMarketPackets(body);
  let view = createMarketView('request');
  for (const packet of [text(), text('delta', 1, 'Información '), text('delta', 2, 'útil 🌎')]) {
    controller.enqueue(new TextEncoder().encode(JSON.stringify(packet) + '\n'));
    view = reduceMarketPacket(view, (await iterator.next()).value);
  }
  assert.equal(view.textBlocks.get('source-web').text, 'Información útil 🌎');
  assert.equal(view.cursor, 0);
  assert.equal(view.blocks.size, 0);
  assert.equal(view.final, null);
  controller.close();
  assert.equal((await iterator.next()).done, true);
});

test('transient duplicates are immutable and missing fragments do not mutate the prior view', () => {
  let view = reduceMarketPacket(createMarketView('request'), text());
  const before = view;
  assert.throws(() => reduceMarketPacket(view, text('delta', 2, 'out of order')), /out of order/);
  assert.equal(before.textBlocks.get('source-web').text, '');
  view = reduceMarketPacket(view, text('delta', 1, 'A'));
  assert.equal(reduceMarketPacket(view, text('delta', 1, 'A')).textBlocks.get('source-web').text, 'A');
  assert.throws(() => reduceMarketPacket(view, text('delta', 1, 'B')), /immutable/);
  assert.throws(() => reduceMarketPacket(view, text('delta', 2, 'B', { turn_id: 'foreign' })), /envelope/);
  assert.equal(view.cursor, 0);
});

test('generation end stays provisional and a later factual discard removes the overlay', () => {
  let view = reduceMarketPacket(createMarketView('request'), text());
  view = reduceMarketPacket(view, text('delta', 1, 'Provisional'));
  view = reduceMarketPacket(view, text('end', 2));
  assert.equal(view.textBlocks.get('source-web').text, 'Provisional');
  assert.equal(view.final, null);
  view = reduceMarketPacket(view, text('discard', 3));
  assert.equal(view.textBlocks.size, 0);
  view = reduceMarketPacket(view, text('delta', 4, 'late'));
  assert.equal(view.textBlocks.size, 0);
});

test('new streams and canonical blocks supersede prior provisional text', () => {
  let view = reduceMarketPacket(createMarketView('request'), text());
  view = reduceMarketPacket(view, text('delta', 1, 'Old'));
  view = reduceMarketPacket(view, text('start', 0, '', { stream_id: 'b'.repeat(32) }));
  assert.equal(view.textBlocks.get('source-web').stream_id, 'b'.repeat(32));
  view = reduceMarketPacket(view, text('delta', 2, 'late old'));
  assert.equal(view.textBlocks.get('source-web').text, '');
  view = reduceMarketPacket(view, envelope({ kind: 'event', event: event(1, {
    block_id: 'source-web', block_revision: 1, source_key: 'web', source_keys: ['web'], text: 'Verified' }) }));
  assert.equal(view.textBlocks.size, 0);
  assert.equal(view.cursor, 1);
  view = reduceMarketPacket(view, text('delta', 1, 'late new', { stream_id: 'b'.repeat(32) }));
  assert.equal(view.textBlocks.size, 0);
});

test('snapshot and canonical response remove transient text and never replay its fragments', () => {
  let view = reduceMarketPacket(createMarketView('request'), text());
  view = reduceMarketPacket(view, text('delta', 1, 'Provisional'));
  view = reduceMarketPacket(view, envelope({ kind: 'snapshot', request_id: 'request',
    replay: { cursor: 0, events: [], snapshot: { blocks: {}, artifacts: {} } }, response: null }));
  assert.equal(view.textBlocks.size, 0);
  view = reduceMarketPacket(view, text('start', 0, '', { stream_id: 'b'.repeat(32) }));
  view = reduceMarketPacket(view, envelope({ kind: 'response', request_id: 'request',
    response: { status: 'complete', markdown: 'Canonical', references: [], artifacts: [] } }));
  view = reduceMarketPacket(view, text('delta', 1, 'late', { stream_id: 'b'.repeat(32) }));
  assert.equal(view.textBlocks.size, 0);
  assert.equal(view.final.markdown, 'Canonical');
  assert.equal(view.cursor, 0);
});

test('obsolete starts retire their late deltas without hiding a newer generation', () => {
  let view = reduceMarketPacket(createMarketView('request'), envelope({ kind: 'event', event: event(1, {
    block_id: 'source-web', block_revision: 2, source_key: 'web', source_keys: ['web'], text: 'Verified' }) }));
  view = reduceMarketPacket(view, text('start', 0, '', { stream_id: 'b'.repeat(32), block_revision: 3 }));
  view = reduceMarketPacket(view, text('delta', 1, 'New generation', { stream_id: 'b'.repeat(32), block_revision: 3 }));
  view = reduceMarketPacket(view, text());
  assert.equal(view.textStreams.get('a'.repeat(32)).retired, true);
  view = reduceMarketPacket(view, text('delta', 1, 'Obsolete'));
  view = reduceMarketPacket(view, text('end', 2));
  assert.equal(view.textBlocks.get('source-web').text, 'New generation');
  assert.equal(view.blocks.get('source-web').text, 'Verified');
  assert.equal(view.cursor, 1);
  assert.throws(() => reduceMarketPacket(view, text('delta', 1, 'Changed')), /immutable/);
  assert.throws(() => reduceMarketPacket(view, text('end', 3, '', { request_id: 'other', turn_id: 'other' })), /another request/);
});

test('public text rejects private fields, split code and its packet budget overflow', () => {
  assert.throws(() => reduceMarketPacket(createMarketView('request'), text('start', 0, '', { reasoning: 'private' })), /envelope/);
  assert.throws(() => reduceMarketPacket(createMarketView('request'), text('start', 0, '', { stream_id: 1e31 })), /envelope/);
  let view = reduceMarketPacket(createMarketView('request'), text());
  assert.throws(() => reduceMarketPacket(view, text('delta', 1, '\ud800')), /fragment/);
  view = reduceMarketPacket(view, text('delta', 1, '``'));
  assert.throws(() => reduceMarketPacket(view, text('delta', 2, '`private')), /prose contract/);
  assert.equal(view.textBlocks.get('source-web').text, '``');
  view = reduceMarketPacket(createMarketView('request'), text());
  for (let index = 1; index < 1023; index++) view = reduceMarketPacket(view, text('delta', index, 'x'));
  view = reduceMarketPacket(view, text('end', 1023));
  assert.throws(() => reduceMarketPacket(view, text('start', 0, '', { stream_id: 'b'.repeat(32) })), /packet budget/);
  assert.equal(view.cursor, 0);
});

test('source activity arrives incrementally before body completion and preserves UI tags', async () => {
  let controller;
  const body = new ReadableStream({ start(value) { controller = value; } });
  const iterator = readMarketPackets(body);
  const progress = event(1, { type: 'source.coverage', block_id: null, evidence_ids: [],
    text: 'Información pendiente de verificar.', coverage: { source: 'facebook', phase: 'retrieving' } });
  const first = new TextEncoder().encode(JSON.stringify(envelope({ kind: 'event', event: progress })) + '\n');
  for (const byte of first) controller.enqueue(Uint8Array.of(byte));
  const packet = await iterator.next();
  assert.equal(packet.done, false);
  let view = reduceMarketPacket(createMarketView('request'), packet.value);
  assert.equal(view.activity.get('facebook').role_key, 'market_analysis');
  assert.equal(view.activity.get('facebook').text, progress.text);
  assert.equal(view.blocks.size, 0);
  assert.equal(view.final, null);
  controller.enqueue(new TextEncoder().encode(JSON.stringify(envelope({ kind: 'response', request_id: 'request',
    response: { status: 'complete', markdown: 'Resumen verificado.', references: [], artifacts: [] } })) + '\n'));
  controller.close();
  view = reduceMarketPacket(view, (await iterator.next()).value);
  assert.equal(view.final.markdown, 'Resumen verificado.');
  assert.equal((await iterator.next()).done, true);
  assert.equal(body.locked, false);
});

test('source activity survives compacted replay and rejects foreign or inconsistent tags', () => {
  const progress = event(3, { type: 'source.coverage', block_id: null, evidence_ids: [],
    coverage: { source: 'facebook', phase: 'retrieved' } });
  const packet = envelope({ kind: 'snapshot', request_id: 'request', replay: { cursor: 3, events: [],
    snapshot: { blocks: {}, artifacts: {}, activity: { facebook: progress } } } });
  const view = reduceMarketPacket(createMarketView('request'), packet);
  assert.equal(view.activity.get('facebook').coverage.phase, 'retrieved');
  assert.equal(view.blocks.size, 0);
  const foreign = structuredClone(packet);
  foreign.replay.snapshot.activity.facebook.turn_id = 'other';
  assert.throws(() => reduceMarketPacket(createMarketView('request'), foreign), /replay activity/);
  const malformed = envelope({ kind: 'event', event: { ...progress, sequence: 1, source_keys: ['instagram'] } });
  assert.throws(() => reduceMarketPacket(createMarketView('request'), malformed), /activity attribution/);
});

test('public stream rejects truncation and cancels its reader on early exit', async () => {
  const truncated = new ReadableStream({ start(controller) {
    controller.enqueue(new TextEncoder().encode('{"protocol":"ai-market-intelligence"}'));
    controller.close();
  } });
  await assert.rejects(async () => { for await (const _packet of readMarketPackets(truncated)) {} }, /inside a public packet/);
  let cancelled = false;
  const body = new ReadableStream({ start(controller) {
    controller.enqueue(new TextEncoder().encode(JSON.stringify(envelope({ kind: 'heartbeat', request_id: 'request' })) + '\n'));
  }, cancel() { cancelled = true; } });
  for await (const _packet of readMarketPackets(body)) break;
  assert.equal(cancelled, true);
  assert.equal(body.locked, false);
});

test('signed media links survive drafts and final responses; unsafe assets fail without mutating state', () => {
  const asset = { kind: 'video', delivery: 'direct', role: 'attachment',
    url: 'https://cdn.example.org/file.mp4?sig=a%2Fb&token=x+y&fbclid=required' };
  const ref = { ...reference, media: [asset], media_content_analyzed: false };
  let view = reduceMarketPacket(createMarketView('request'), envelope({ kind: 'event', event: event(1), references: [ref] }));
  assert.equal(view.references.get('source').media[0].url, asset.url);
  view = reduceMarketPacket(view, envelope({ kind: 'response', request_id: 'request',
    response: { status: 'complete', references: [ref], artifacts: [] } }));
  assert.deepEqual(view.final.references[0].media, [asset]);
  for (const url of ['javascript:alert(1)', 'http://cdn.example.org/a', 'https://127.0.0.1/a',
    'https://[::1]/a', 'https://localhost/a', 'https://user:password@example.org/a']) {
    assert.throws(() => reduceMarketPacket(createMarketView('request'), envelope({ kind: 'event', event: event(1),
      references: [{ ...ref, media: [{ ...asset, url }] }] })), /Unsafe/);
  }
});

test('stable source and analyst keys survive drafts, deduplication and JSON key order changes', () => {
  const packet = envelope({ kind: 'event', event: event(1), references: [reference] });
  const view = reduceMarketPacket(createMarketView('request'), packet);
  assert.equal(view.blocks.get('source-facebook').source_key, 'facebook');
  assert.equal(view.blocks.get('source-facebook').role_key, 'market_analysis');
  assert.equal(view.references.get('source').url, reference.url);
  packet.event = Object.fromEntries(Object.entries(packet.event).reverse());
  assert.equal(reduceMarketPacket(view, packet).blocks.size, 1);
  packet.event.text = 'A changed event';
  assert.throws(() => reduceMarketPacket(view, packet), /immutable/);
  assert.equal(view.blocks.get('source-facebook').text, 'Hallazgo atribuido.');
});

test('gaps request replay without mutating the current view; foreign requests and private frames fail', () => {
  const view = createMarketView('request');
  assert.throws(() => reduceMarketPacket(view, envelope({ kind: 'event', event: event(2) })), ReplayRequired);
  assert.equal(view.cursor, 0);
  assert.throws(() => reduceMarketPacket(view, envelope({ kind: 'event', event: event(1, { turn_id: 'other' }) })), /another request/);
  assert.throws(() => reduceMarketPacket(view, { type: 'tool_output', content: 'PRIVATE' }), /normalized public/);
  assert.throws(() => reduceMarketPacket(view, envelope({ kind: 'event', event: event(1), references: [
    { ...reference, url: 'javascript:alert(1)' }] })), /Unsafe/);
});

test('a withdrawn draft hides its provisional chart and the final artifact set is authoritative', () => {
  let view = reduceMarketPacket(createMarketView('request'), envelope({ kind: 'event', event: event(1), artifacts: [artifact] }));
  assert.equal(view.artifacts.size, 1);
  view = reduceMarketPacket(view, envelope({ kind: 'event', event: event(2, {
    type: 'draft.corrected', status: 'failed', supersedes: ['event-1'], text: 'Borrador retirado.' }) }));
  assert.equal(view.artifacts.size, 0);
  view = reduceMarketPacket(view, envelope({ kind: 'response', request_id: 'request',
    response: { status: 'partial', markdown: 'Conclusión acotada.', references: [], artifacts: [] } }));
  assert.equal(view.final.status, 'partial');
  assert.equal(view.artifacts.size, 0);
});

test('fresh replay restores source links and analyst icons while filtering a withdrawn artifact', () => {
  const retired = event(3, { type: 'draft.corrected', status: 'failed', role_key: 'financial_analysis' });
  const packet = envelope({ kind: 'snapshot', request_id: 'request', references: [reference], artifacts: [artifact],
    replay: { cursor: 3, events: [], snapshot: { blocks: { 'source-facebook': retired }, artifacts: { plot: artifact } } }, response: null });
  const view = reduceMarketPacket(createMarketView('request'), packet);
  assert.equal(view.cursor, 3);
  assert.equal(view.references.get('source').source, 'facebook');
  assert.equal(view.blocks.get('source-facebook').role_key, 'financial_analysis');
  assert.equal(view.artifacts.size, 0);
});

test('pending or resumable failure stays provisional rather than becoming a complete answer', () => {
  const view = reduceMarketPacket(createMarketView('request'), envelope({ kind: 'response', request_id: 'request',
    response: { status: 'failed', resumable: true, markdown: 'Recover the same request.', references: [], artifacts: [] } }));
  assert.equal(view.final, null);
  assert.equal(view.response.resumable, true);
});

test('duplicated preview events cannot restore an artifact excluded from the final answer', () => {
  const preview = envelope({ kind: 'event', event: event(1), artifacts: [artifact] });
  let view = reduceMarketPacket(createMarketView('request'), preview);
  view = reduceMarketPacket(view, envelope({ kind: 'response', request_id: 'request',
    response: { status: 'complete', references: [], artifacts: [] } }));
  view = reduceMarketPacket(view, preview);
  assert.equal(view.artifacts.size, 0);
  assert.equal(view.cursor, 1);
});

test('an event represented by a recovered block is already applied after a snapshot', () => {
  let view = reduceMarketPacket(createMarketView('request'), envelope({ kind: 'snapshot', request_id: 'request',
    replay: { cursor: 3, events: [], snapshot: { blocks: { 'source-facebook': event(3) }, artifacts: {} } }, response: null }));
  view = reduceMarketPacket(view, envelope({ kind: 'event', event: event(3) }));
  assert.equal(view.cursor, 3);
  assert.equal(view.blocks.size, 1);
  assert.throws(() => reduceMarketPacket(view, envelope({ kind: 'event', event: event(3, { text: 'Changed' }) })), /immutable/);
});

test('optional content items reuse their exact reference manifest and stay outside the durable event', () => {
  const packet = contentPacket(contentFixture.collection);
  const view = reduceMarketPacket(createMarketView('request'), packet);
  assert.deepEqual(view.contentItems, contentFixture.collection);
  assert.equal(view.contentItems.items[0].comments_analysis.analyzed_count, 1);
  assert.equal(view.cursor, 1);
  assert.equal(packet.event.content_items, undefined);
  assert.equal(view.references.get('comment-evidence').requested_url, view.references.get('post-evidence').url);
  const legacy = reduceMarketPacket(createMarketView('fixture-request'), contentFixture.legacy_response);
  assert.equal(legacy.contentItems, null);
  assert.equal(legacy.final.status, 'complete');
});

test('content collection and item revisions are monotonic and equal revisions are immutable', () => {
  let view = reduceMarketPacket(createMarketView('request'), contentPacket(contentFixture.collection));
  const changed = structuredClone(contentFixture.collection);
  changed.items[0].text = 'Una síntesis distinta sobre la evidencia admitida.';
  assert.throws(() => reduceMarketPacket(view, contentPacket(changed, 2)), /immutable collection/);
  changed.revision = 2;
  assert.throws(() => reduceMarketPacket(view, contentPacket(changed, 2)), /immutable item/);
  changed.items[0].item_revision = 2;
  view = reduceMarketPacket(view, contentPacket(changed, 2));
  view = reduceMarketPacket(view, contentPacket(contentFixture.collection, 3));
  assert.equal(view.contentItems.revision, 2);
  assert.equal(view.contentItems.items[0].item_revision, 2);
  const empty = { schema_version: '1.0', revision: 3, items: [] };
  view = reduceMarketPacket(view, contentPacket(empty, 4));
  assert.equal(view.contentItems.items.length, 0);
  assert.throws(() => reduceMarketPacket(view, contentPacket({ ...contentFixture.collection, revision: 4 }, 5)), /revision regressed/);
  assert.equal(view.cursor, 4);
});

test('snapshot restores content items and the canonical response retracts them without late revival', () => {
  let view = reduceMarketPacket(createMarketView('request'), envelope({ kind: 'snapshot', request_id: 'request',
    references: contentFixture.references, artifacts: [], content_items: contentFixture.collection,
    replay: { cursor: 2, events: [], snapshot: { blocks: {}, artifacts: {} } }, response: null }));
  assert.equal(view.contentItems.items.length, 1);
  assert.equal(view.cursor, 2);
  const empty = { schema_version: '1.0', revision: 2, items: [] };
  view = reduceMarketPacket(view, envelope({ kind: 'response', request_id: 'request',
    references: [], artifacts: [], content_items: empty,
    response: { status: 'partial', markdown: 'No quedaron unidades verificadas.', references: [], artifacts: [] } }));
  view = reduceMarketPacket(view, contentPacket({ ...contentFixture.collection, revision: 3 }, 3));
  assert.equal(view.contentItems.items.length, 0);
  assert.equal(view.final.status, 'partial');
});

test('unknown item schema is ignored while invalid known schema fails atomically', () => {
  const initial = createMarketView('request');
  const unknown = reduceMarketPacket(initial, contentPacket({ schema_version: 'future', items: 'opaque' }));
  assert.equal(unknown.contentItems, null);
  assert.equal(unknown.cursor, 1);
  const invalid = structuredClone(contentFixture.collection);
  invalid.items[0].primary_evidence_id = 'missing';
  assert.throws(() => reduceMarketPacket(initial, contentPacket(invalid)), /primary evidence/);
  assert.equal(initial.cursor, 0);
  assert.equal(initial.references.size, 0);
  assert.equal(initial.contentItemHistory.size, 0);
});

test('content items reject foreign evidence and comment parents without adding source-specific UI rules', () => {
  const mutations = [
    value => { value.references[0].source = 'other-source'; },
    value => { value.references[0].content_basis = 'comment_text'; },
    value => { value.references[1].requested_url = 'https://example.org/other-parent'; },
    value => { value.collection.items[0].comments_analysis.parent_item_id = 'different-item'; },
    value => { value.collection.items[0].comments_analysis.text = null; },
    value => { value.collection.items[0].comments_analysis.analyzed_count = 2; },
    value => { value.collection.items[0].text = 'Consulta https://example.org para más detalles.'; },
    value => { value.collection.items[0].private_receipt = 'private'; },
  ];
  for (const mutate of mutations) {
    const value = structuredClone(contentFixture);
    mutate(value);
    assert.throws(() => reduceMarketPacket(createMarketView('request'), contentPacket(value.collection, 1, value.references)), /content items/);
  }
  const futureSource = structuredClone(contentFixture);
  for (const row of futureSource.references) row.source = 'future-feed';
  futureSource.collection.items[0].source_key = 'future-feed';
  const view = reduceMarketPacket(createMarketView('request'), contentPacket(futureSource.collection, 1, futureSource.references));
  assert.equal(view.contentItems.items[0].source_key, 'future-feed');
});

test('content collection bounds count UTF8 bytes and reject oversized or nonfinite revisions', () => {
  for (const revision of [0, true, Number.MAX_SAFE_INTEGER + 1])
    assert.throws(() => reduceMarketPacket(createMarketView('request'), contentPacket({ ...contentFixture.collection, revision })), /revision/);
  const tooMany = { ...contentFixture.collection, items: Array(401).fill(contentFixture.collection.items[0]) };
  assert.throws(() => reduceMarketPacket(createMarketView('request'), contentPacket(tooMany)), /item bound/);
  const tooLarge = { ...contentFixture.collection, items: Array.from({ length: 100 }, () => ({
    ...contentFixture.collection.items[0], text: '🌎'.repeat(3000) })) };
  assert.throws(() => reduceMarketPacket(createMarketView('request'), contentPacket(tooLarge)), /byte bound/);
});

test('content prose accepts brand names while rejecting executable and Markdown links', () => {
  for (const text of ['Marca.com informó una oferta.', 'La sigla S.A. se conserva.', 'www.marca.com es el nombre observado.']) {
    const collection = structuredClone(contentFixture.collection);
    collection.items[0].text = text;
    assert.equal(reduceMarketPacket(createMarketView('request'), contentPacket(collection)).contentItems.items[0].text, text);
  }
  for (const text of ['javascript:alert(1)', 'data:text/html,valor', '[Oferta](destino)', 'HTTPS:ejemplo']) {
    const collection = structuredClone(contentFixture.collection);
    collection.items[0].text = text;
    assert.throws(() => reduceMarketPacket(createMarketView('request'), contentPacket(collection)), /URL in prose/);
  }
});

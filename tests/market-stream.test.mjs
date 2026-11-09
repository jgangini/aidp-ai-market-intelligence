import test from 'node:test';
import assert from 'node:assert/strict';
import { createMarketView, reduceMarketPacket, ReplayRequired, readMarketPackets } from '../scripts/market-stream-example.mjs';

const envelope = values => ({ protocol: 'ai-market-intelligence', version: '2.0', ...values });
const event = (sequence, values = {}) => ({ event_id: `event-${sequence}`, sequence, turn_id: 'request',
  type: 'draft.upsert', block_id: 'source-facebook', status: 'provisional', role_key: 'market_analysis',
  source_key: 'facebook', source_keys: ['facebook'], evidence_ids: ['source'], text: 'Hallazgo atribuido.', ...values });
const reference = { evidence_id: 'source', url: 'https://example.org/public', source: 'facebook' };
const artifact = { artifact_id: 'plot', kind: 'line', evidence_ids: ['source'], rows: [] };

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

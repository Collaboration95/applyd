'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const telemetry = require('../scripts/telemetry.js');

test('live tool boundary emits durable open then same-process exact duration with correlation and offset', async () => {
  const rows = []; let mono = 900, wall = Date.parse('2026-10-20T01:00:00Z');
  const t = telemetry.create({ context: { run_id: 'synthetic-run', application_id: 'synthetic-app', attempt_id: 'attempt', action_id: '1', parent_id: 'outer' },
    processId: 'browser-process', emit: row => rows.push(row), monotonic: () => mono, wall: () => new Date(wall) });
  assert.equal(await t.call('navigation', 'browser', () => { mono += 3; wall += 3000; return 42; }), 42);
  assert.equal(rows.length, 2); assert.equal(rows[0].outcome, 'open'); assert.equal(rows[0].duration_seconds, null);
  assert.equal(rows[1].duration_seconds, 3); assert.equal(rows[1].parent_id, 'outer');
  assert.equal(rows[1].started_at, '2026-10-20T09:00:00.000+08:00');
  assert.equal(rows[1].process_id, 'browser-process'); assert.equal(rows[1].attempt_id, 'attempt');
});

test('telemetry transport failures never replay tools or fabricate successful browser outcomes', async () => {
  let calls = 0;
  const t = telemetry.create({ context: { run_id: 'fixture' }, processId: 'browser', emit: () => { throw Error('private'); } });
  assert.equal(await t.call('submission', 'browser', () => ++calls), 1);
  assert.equal(calls, 1); assert.equal(t.available, false);
  await assert.rejects(t.call('submission', 'browser', () => { calls++; throw Error('tool_failed'); }), /tool_failed/);
  assert.equal(calls, 2);
});

test('concurrent calls have explicit independent parents and matching model generation usage', async () => {
  let finish; const rows = [];
  const t = telemetry.create({ context: {run_id:'fixture'}, processId:'model-process', emit: r => rows.push(r) });
  const first = t.call('answer_resolution', 'model', () => new Promise(resolve => { finish = resolve; }));
  await new Promise(resolve => setImmediate(resolve));
  await t.call('review', 'tool', () => true); finish(true); await first;
  assert.equal(rows[0].parent_id, null); assert.equal(rows[1].parent_id, null);
  assert.notEqual(rows[0].span_id, rows[1].span_id);
});

test('tokens per second uses actual output usage with matching monotonic generation interval', async () => {
  let seconds=0;const rows=[];
  const t=telemetry.create({context:{run_id:'fixture'},processId:'model',monotonic:()=>seconds,emit:r=>rows.push(r)});
  const model={provider:'synthetic',model:'fixture-model',configuration:'default'};
  const result=await t.call('answer_resolution','model',()=>{seconds+=2;return {output_tokens:10,value:42};},{model,outputUsage:r=>r.output_tokens});
  assert.equal(result.value,42);assert.equal(rows[1].model.output_tokens,10);assert.equal(rows[1].model.generation_seconds,2);
  await t.call('review','model',()=>true,{model});assert.equal(rows[3].model.output_tokens,null);assert.equal(rows[3].model.generation_seconds,null);
});

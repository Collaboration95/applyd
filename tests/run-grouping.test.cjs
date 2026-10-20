const test = require('node:test');
const assert = require('node:assert/strict');
const { groupRuns } = require('../dashboard/runs.js');
const app = (id, at) => ({ id, status: 'submitted', updated_at: at, submissions: [{ at, resume: { id: `sent-${id}` } }] });

test('consecutive gaps up to one hour stay together; larger gaps start a run', () => {
  const runs = groupRuns([app('a', '2026-10-18T09:00:00+08:00'), app('b', '2026-10-18T10:00:00+08:00'), app('c', '2026-10-18T10:59:00+08:00'), app('d', '2026-10-18T12:00:00+08:00')]);
  assert.deepEqual(runs.map(r => r.entries.map(e => e.app.id)), [['d'], ['a', 'b', 'c']]);
  assert.deepEqual(runs.map(r => r.number), [2, 1]);
});
test('midnight and different timestamp offsets do not split a run', () => {
  const runs = groupRuns([app('a', '2026-10-18T23:50:00+08:00'), app('b', '2026-10-18T16:20:00Z')]);
  assert.equal(runs.length, 1);
  assert.deepEqual(runs[0].entries.map(e => e.app.id), ['a', 'b']);
});
test('follow-ups and newer prepared resumes do not move a submitted job', () => {
  const a = { ...app('a', '2026-10-18T09:00:00+08:00'), status: 'interview', updated_at: '2026-10-21T12:00:00+08:00', prepared: { at: '2026-10-21T12:00:00+08:00', resume: { id: 'unused-newer' } } };
  const runs = groupRuns([a, app('b', '2026-10-18T09:20:00+08:00')]);
  assert.equal(runs.length, 1);
  assert.equal(runs[0].entries[0].resume.id, 'sent-a');
  assert.equal(runs[0].entries[0].status, 'submitted');
});
test('unsent entries do not claim to be applications', () => {
  const runs = groupRuns([{ id: 'waiting', status: 'awaiting_answer', updated_at: '2026-10-18T09:10:00+08:00' }, app('sent', '2026-10-18T09:20:00+08:00')]);
  assert.equal(runs.length, 1);
  assert.equal(runs[0].entries.filter(e => e.kind === 'submitted').length, 1);
});
test('missing timestamps remain visible without an invented time', () => {
  const runs = groupRuns([{ id: 'unknown', status: 'preparing' }]);
  assert.equal(runs[0].id, 'undated');
  assert.equal(runs[0].started_at, null);
});

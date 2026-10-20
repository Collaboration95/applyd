const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { groupRuns, normalizeApplications, assignRunNumbers, mergePayloads, availableStatuses, matchesCurrentStatus } = require('../dashboard/runs.js');
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
test('malformed records are counted and valid records remain renderable', () => {
  const valid = { ...app('valid', '2026-10-18T09:00:00+08:00'), company: 'Example', title: 'Engineer' };
  const duplicate = { ...valid, updated_at: '2026-10-18T10:00:00+08:00' };
  const result = normalizeApplications([valid, null, { id: 'missing-title', company: 'Example', status: 'ready' }, duplicate]);
  assert.equal(result.skipped, 3);
  assert.deepEqual(result.applications.map(item => item.id), ['valid']);
  assert.equal(groupRuns(result.applications)[0].entries[0].app.id, 'valid');
});
test('importing older records preserves existing run numbers and numbers by start time', () => {
  const current = groupRuns([app('newer', '2026-10-18T11:00:00+08:00'), app('older', '2026-10-18T09:00:00+08:00')]);
  const saved = assignRunNumbers(current, {});
  assert.equal(saved['run-' + Date.parse('2026-10-18T09:00:00+08:00')], 1);
  assert.equal(saved['run-' + Date.parse('2026-10-18T11:00:00+08:00')], 2);
  const withImportedOld = groupRuns([app('imported', '2026-10-18T07:00:00+08:00'), app('newer', '2026-10-18T11:00:00+08:00'), app('older', '2026-10-18T09:00:00+08:00')]);
  const nextSaved = assignRunNumbers(withImportedOld, saved);
  assert.equal(nextSaved['run-' + Date.parse('2026-10-18T07:00:00+08:00')], 0);
  assert.equal(nextSaved['run-' + Date.parse('2026-10-18T09:00:00+08:00')], 1);
  assert.equal(nextSaved['run-' + Date.parse('2026-10-18T11:00:00+08:00')], 2);
});
test('multi-file imports retain top-level payload keys and newest application versions', () => {
  const result = mergePayloads([
    { generated_at: '2026-10-18T10:00:00Z', source: 'older-file', schema_version: 1, applications: [app('same', '2026-10-18T09:00:00Z')] },
    { generated_at: '2026-10-19T10:00:00Z', custom_export_note: 'kept', applications: [{ ...app('same', '2026-10-18T09:00:00Z'), updated_at: '2026-10-19T09:00:00Z' }] }
  ]);
  assert.equal(result.source, 'older-file');
  assert.equal(result.schema_version, 1);
  assert.equal(result.custom_export_note, 'kept');
  assert.equal(result.generated_at, '2026-10-19T10:00:00Z');
  assert.equal(result.applications.length, 1);
  assert.equal(result.applications[0].updated_at, '2026-10-19T09:00:00Z');
});
test('status options come from current data and historical submission status does not match', () => {
  const item = { ...app('later-status', '2026-10-18T09:00:00Z'), status: 'interview' };
  assert.deepEqual(availableStatuses([item]), ['interview']);
  assert.equal(matchesCurrentStatus(item, 'interview'), true);
  assert.equal(matchesCurrentStatus(item, 'submitted'), false);
});
test('an older import joining an existing run preserves its number and invalid import count', () => {
  const current = groupRuns([app('existing', '2026-10-18T09:00:00Z')]);
  const saved = assignRunNumbers(current);
  const imported = groupRuns([app('existing', '2026-10-18T09:00:00Z'), app('adjacent', '2026-10-18T08:30:00Z')]);
  assignRunNumbers(imported, saved);
  assert.equal(imported[0].number, current[0].number);
  const merged = mergePayloads([{ applications: [null, { ...app('existing', '2026-10-18T09:00:00Z'), company: 'Example', title: 'Engineer' }] }]);
  assert.equal(normalizeApplications(merged.applications).skipped, 1);
});
test('static dashboard keeps local-file scripts and run grouping without a fixed timezone suffix', () => {
  const html = fs.readFileSync(require.resolve('../dashboard/index.html'), 'utf8');
  const script = fs.readFileSync(require.resolve('../dashboard/app.js'), 'utf8');
  assert.match(html, /src="control\.js"/);
  assert.match(html, /data-tab="runs"/);
  assert.match(html, /data-tab="questions"/);
  assert.doesNotMatch(script, /SGT/);
  assert.match(script, /const allRuns = window\.JobRuns\.groupRuns\(allRecords\(\)\)/);
});

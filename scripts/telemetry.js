/* Same private v2 span contract as spans.py. Trusted transport persists start/end
 * under the workspace writer. No network, browser driver, retry or raw content. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.ApplydTelemetry = factory();
})(globalThis, function () {
  'use strict';
  const phases = new Set('workflow startup context_loading saved_discovery card_screening navigation listing_capture eligibility answer_resolution resume_resolution resume_reuse resume_upload file_picker form_observation form_filling form_readback review preflight submission confirmation persistence export retry handoff orchestration'.split(' '));
  const channels = new Set(['browser', 'model', 'tool', 'user_wait', 'blocked_wait', 'orchestration']);
  const counters = new Set(['uploads', 'repeated_uploads', 'skipped_fields', 'field_writes', 'record_reads', 'exports', 'observations', 'redundant_observations', 'retries']);
  function create({ context, emit, monotonic = () => performance.now() / 1000,
    wall = () => new Date(), processId = globalThis.crypto?.randomUUID?.() } = {}) {
    if (!context?.run_id || !processId || typeof emit !== 'function') throw new Error('telemetry_context_required');
    let available = true, sequence = 0;
    // Explicit parent correlation rather than a shared async stack: overlapping
    // browser/model promises must remain siblings unless passed a child context.
    const stamp = () => {
      const date = wall();
      return new Date(date.getTime() + 8 * 3600000).toISOString().replace('Z', '+08:00');
    };
    const send = async row => {
      if (available) try { if (await emit(JSON.parse(JSON.stringify(row))) === false) available = false; }
      catch { available = false; }
    };
    async function call(phase, channel, fn, options = {}) {
      if (!phases.has(phase) || !channels.has(channel) || Object.entries(options.counters || {}).some(([k,v]) => !counters.has(k) || !Number.isSafeInteger(v) || v < 0)) throw new Error('invalid_span_label');
      const start = monotonic();
      const spanId = globalThis.crypto?.randomUUID?.() || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}-${++sequence}`;
      const row = { schema_version: 2, run_id: context.run_id,
        application_id: context.application_id ?? null, attempt_id: context.attempt_id ?? null,
        action_id: context.action_id ?? null, span_id: spanId,
        parent_id: options.parent_id ?? context.parent_id ?? null, process_id: processId,
        phase, channel, started_at: stamp(), ended_at: null, duration_seconds: null,
        outcome: 'open', coverage: 'open', counters: options.counters || {}, model: options.model ? { provider: options.model.provider, model: options.model.model, configuration: options.model.configuration, output_tokens: null, generation_seconds: null } : null };
      await send(row); // reservation must be acknowledged before crossing boundary
      try {
        const value = await fn({ ...context, parent_id: row.span_id });
        if (row.model && channel === 'model' && typeof options.outputUsage === 'function') {
          // Usage extraction must not change or replay an already completed tool.
          try { const tokens = options.outputUsage(value);
            if (Number.isSafeInteger(tokens) && tokens >= 0) row.model.output_tokens = tokens;
          } catch { /* unmeasured usage remains null */ }
        }
        row.outcome = 'ok'; return value;
      } catch (error) { row.outcome = 'failed'; throw error; }
      finally {
        row.ended_at = stamp(); row.duration_seconds = monotonic() - start;
        row.coverage = Number.isFinite(row.duration_seconds) && row.duration_seconds >= 0 ? 'measured' : 'unknown';
        if (row.coverage === 'unknown') row.duration_seconds = null;
        if (row.model && channel === 'model' && row.model.output_tokens !== null && row.coverage === 'measured') row.model.generation_seconds = row.duration_seconds;
        await send(row); // never call fn again when persistence fails
      }
    }
    return Object.freeze({ call, get available() { return available; } });
  }
  return Object.freeze({ create });
});

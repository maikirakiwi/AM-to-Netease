(() => {
  const root = document.getElementById('run-progress');
  const bar = document.getElementById('run-bar');
  const status = document.getElementById('run-status');
  const counts = document.getElementById('run-counts');
  const current = document.getElementById('run-current');
  const outcomes = document.getElementById('run-outcomes');
  const error = document.getElementById('run-error');
  async function poll() {
    try {
      const response = await fetch(`/api/runs/${root.dataset.runId}`, {
        cache: 'no-store', signal: AbortSignal.timeout(10000),
      });
      if (!response.ok) throw new Error(`Status request failed (${response.status})`);
      const run = await response.json();
      const labels = {running: 'Sync in progress', stopping: 'Stopping after the current request…', reverting: 'Reverting run changes…', success: 'Sync completed', partial: 'Sync finished — some songs need review', cancelled: 'Run stopped and changes reverted', rollback_failed: 'Revert incomplete — retry required', interrupted: 'Run interrupted by a restart', failed: 'Sync failed'};
      status.textContent = labels[run.status] || run.status;
      const active = ['running', 'stopping', 'reverting'].includes(run.status);
      const percent = document.getElementById('run-percent');
      if (percent) percent.textContent = run.total ? `${Math.round(run.processed / run.total * 100)}%` : '';
      for (const key of ['added', 'skipped', 'failed', 'ambiguous']) {
        const node = document.getElementById(`count-${key}`);
        if (node) node.textContent = run[key];
      }
      const stopForm = document.getElementById('stop-form');
      const stopButton = document.getElementById('stop-button');
      if (stopForm) stopForm.hidden = !active && !['rollback_failed', 'interrupted'].includes(run.status);
      if (stopButton) {
        stopButton.disabled = ['stopping', 'reverting'].includes(run.status);
        stopButton.textContent = ['rollback_failed', 'interrupted'].includes(run.status) ? 'Retry revert' : 'Stop & revert changes';
      }
      if (run.total === null) {
        bar.removeAttribute('value');
        counts.textContent = active ? 'Loading source playlist; item count pending…' : 'Run ended before the source playlist finished loading.';
      } else {
        bar.max = run.total || 1;
        bar.value = run.total === 0 && run.status !== 'running' ? 1 : run.processed;
        counts.textContent = `${run.processed} / ${run.total} songs processed · ${run.remaining} items left`;
      }
      current.textContent = active
        ? (run.current_source_title ? `Current song: ${run.current_source_title}` : 'Preparing playlists…')
        : 'Run finished.';
      outcomes.textContent = `Added: ${run.added} · Skipped: ${run.skipped} · Removed: ${run.removed} · Failed: ${run.failed} · Needs review: ${run.ambiguous}`;
      error.textContent = run.error || '';
      if (!active) return;
    } catch (failure) {
      error.textContent = `${failure.message}. Retrying status update…`;
    }
    setTimeout(poll, 1000);
  }
  poll();
})();

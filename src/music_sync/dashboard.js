(() => {
  document.addEventListener('click', event => {
    const control = event.target.closest('[data-confirm]');
    if (control && !window.confirm(control.dataset.confirm)) event.preventDefault();
  });
  function openSong() {
    const target = document.getElementById(decodeURIComponent(location.hash.slice(1)));
    if (target && target.tagName === 'DETAILS') target.open = true;
  }
  const correction = document.getElementById('correction-status');
  async function pollCorrection() {
    const message = correction.querySelector('[data-correction-message]');
    try {
      const response = await fetch(`/api/runs/${correction.dataset.runId}`, {
        cache: 'no-store', signal: AbortSignal.timeout(10000),
      });
      if (!response.ok) throw new Error(`Status request failed (${response.status})`);
      const run = await response.json();
      if (run.status === 'success') {
        const url = new URL(location.href);
        url.searchParams.delete('correction_run');
        location.replace(url);
        return;
      }
      if (!['running', 'stopping', 'reverting'].includes(run.status)) {
        message.textContent = run.status === 'cancelled' ? 'Correction cancelled and changes reverted.'
          : `Correction ${run.status}: ${run.error || 'Open progress for details.'}`;
        correction.classList.replace('info', 'warning');
        return;
      }
      message.textContent = run.status === 'running'
        ? 'Applying correction in the background. You can keep using the dashboard.'
        : `Correction ${run.status}…`;
    } catch (error) {
      message.textContent = `${error.message}. Retrying correction status…`;
    }
    setTimeout(pollCorrection, 1000);
  }
  if (correction) pollCorrection();
  openSong();
  window.addEventListener('hashchange', openSong);
})();

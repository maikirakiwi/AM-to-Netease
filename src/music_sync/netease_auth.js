(() => {
  const login = document.getElementById('netease-login');
  if (!login) return;
  const message = document.getElementById('netease-login-status');
  const messages = {
    waiting: 'Waiting for you to scan the QR code…',
    scanned: 'QR code scanned. Confirm login in the Netease Music app.',
    authorized: 'Login successful. Your Netease session has been saved. You can return to settings.',
    expired: 'This QR code has expired. Generate a new QR code to try again.',
    failed: 'Login failed. Generate a new QR code to try again. Details are in the service logs.',
  };
  async function poll() {
    try {
      const response = await fetch(`/setup/netease/${login.dataset.attempt}/check`, {
        method: 'POST', cache: 'no-store', signal: AbortSignal.timeout(15000),
      });
      if (response.status === 410) {
        message.textContent = 'This login is no longer active. Generate a new QR code.';
        return;
      }
      if (!response.ok) throw new Error('Could not check login status');
      const {status} = await response.json();
      message.textContent = messages[status] || messages.failed;
      if (!['waiting', 'scanned'].includes(status)) return;
    } catch (error) {
      message.textContent = `${error.message}. Retrying…`;
    }
    setTimeout(poll, 2000);
  }
  poll();
})();

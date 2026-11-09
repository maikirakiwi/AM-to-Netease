// Apple popup handshake, following Citadel's appleAuthorize.ts flow.
const authorizeButton = document.getElementById('authorize');
const authStatus = document.getElementById('status');
const developerToken = JSON.parse(document.getElementById('apple-config').textContent);

authorizeButton.onclick = () => {
  if (!developerToken) {
    authStatus.textContent = 'Developer token unavailable. Restart the service to retry fetching from Apple.';
    return;
  }
  const popup = window.open('about:blank', 'apple-music-service-view', 'width=650,height=650');
  if (!popup) {
    authStatus.textContent = 'Popup blocked. Allow popups for this dashboard and try again.';
    return;
  }
  authorizeButton.disabled = true;
  authStatus.textContent = 'Waiting for Apple Music authorization…';
  let capturedToken = null;
  let finished = false;
  let graceTimer;
  let pollTimer;
  let timeoutTimer;
  const thirdPartyInfo = (parameters) => JSON.stringify({
    thirdPartyName: location.host,
    thirdPartyToken: developerToken,
    thirdPartyParameters: parameters,
  });
  const cleanup = () => {
    window.removeEventListener('message', handler);
    clearInterval(pollTimer);
    clearTimeout(timeoutTimer);
    clearTimeout(graceTimer);
  };
  const fail = (message) => {
    if (finished) return;
    finished = true;
    cleanup();
    popup.close();
    authorizeButton.disabled = false;
    authStatus.textContent = message;
  };
  const save = async () => {
    if (finished) return;
    finished = true;
    cleanup();
    authStatus.textContent = 'Verifying Apple Music credentials…';
    try {
      const response = await fetch('/setup/apple', {
        method: 'POST',
        headers: {'Content-Type': 'application/x-www-form-urlencoded'},
        body: new URLSearchParams({user_token: capturedToken}),
      });
      if (!response.ok) throw new Error(await response.text());
      authStatus.textContent = 'Authorized and verified. Return to the dashboard.';
    } catch (error) {
      authStatus.textContent = 'Apple Music verification failed: ' + String(error);
    } finally {
      capturedToken = null;
      authorizeButton.disabled = false;
    }
  };
  const handler = (event) => {
    if (event.source !== popup) return;
    let origin;
    try { origin = new URL(event.origin); } catch { return; }
    if (origin.protocol !== 'https:' || !(
      origin.hostname === 'apple.com' || origin.hostname.endsWith('.apple.com') ||
      origin.hostname === 'apps.mzstatic.com' || origin.hostname.endsWith('.apps.mzstatic.com')
    )) return;
    const request = event.data;
    if (!request || request.jsonrpc !== '2.0' || typeof request.id !== 'number' || typeof request.method !== 'string') return;
    const respond = (result) => popup.postMessage({jsonrpc: '2.0', id: request.id, result}, event.origin);
    switch (request.method) {
      case 'thirdPartyInfo':
        respond(thirdPartyInfo(request.params?.[0]));
        break;
      case 'authorize':
        respond(null);
        if (!Array.isArray(request.params) || typeof request.params[0] !== 'string' || !request.params[0].trim()) {
          fail('Apple returned an invalid user token. Try again.');
          return;
        }
        capturedToken = request.params[0];
        // Let Apple finish its handshake and close before saving the token.
        if (!graceTimer) graceTimer = setTimeout(() => popup.close(), 2000);
        break;
      case 'close':
        respond(null);
        break;
      case 'decline':
      case 'switchUserId':
        respond(null);
        fail('Apple Music authorization was cancelled.');
        break;
      case 'unavailable':
        respond(null);
        fail('Apple Music is unavailable for this account. Check your subscription.');
        break;
    }
  };
  window.addEventListener('message', handler);
  pollTimer = setInterval(() => {
    if (!popup.closed) return;
    if (capturedToken) void save();
    else fail('Authorization window closed before a token was received.');
  }, 500);
  timeoutTimer = setTimeout(() => fail('Apple Music authorization timed out. Try again.'), 300000);
  const encoded = btoa(unescape(encodeURIComponent(thirdPartyInfo())));
  popup.location.href = 'https://authorize.music.apple.com/woa?a=' + encodeURIComponent(encoded) + '&referrer=' + encodeURIComponent(location.href);
};

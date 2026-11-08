(() => {
  document.addEventListener('click', event => {
    const control = event.target.closest('[data-confirm]');
    if (control && !window.confirm(control.dataset.confirm)) event.preventDefault();
  });
  function openSong() {
    const target = document.getElementById(decodeURIComponent(location.hash.slice(1)));
    if (target && target.tagName === 'DETAILS') target.open = true;
  }
  openSong();
  window.addEventListener('hashchange', openSong);
})();

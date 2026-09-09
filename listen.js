(() => {
  const button = document.getElementById('listen');
  const status = document.getElementById('listen-status');
  const audio = new Audio();
  audio.preload = 'none';
  let wanted = false;
  let available = false;
  let attempt = 0;

  function stop(message = '') {
    wanted = false;
    attempt++;
    audio.pause();
    audio.removeAttribute('src');
    audio.load();
    button.textContent = 'Listen';
    button.setAttribute('aria-pressed', 'false');
    status.textContent = message;
  }

  button.addEventListener('click', async () => {
    if (wanted) { stop(); return; }
    if (!available) return;
    wanted = true;
    const current = ++attempt;
    button.textContent = 'Cancel';
    button.setAttribute('aria-pressed', 'true');
    status.textContent = 'Connecting to live audio...';
    audio.src = '/audio.mp3?live=' + Date.now();
    try {
      await audio.play();
      if (current !== attempt) return;
      button.textContent = 'Mute';
      status.textContent = '';
    } catch (_) {
      if (current !== attempt) return;
      stop('Couldn't play audio. Tap to retry.');
      button.textContent = 'Retry audio';
    }
  });

  audio.addEventListener('error', () => {
    if (!wanted) return;
    stop('Audio interrupted. Tap to reconnect.');
    button.textContent = 'Retry audio';
  });
  audio.addEventListener('ended', () => {
    if (wanted) stop('Live audio has ended.');
  });

  async function check() {
    try {
      const response = await fetch('/status', {cache: 'no-store'});
      const state = await response.json();
      available = response.ok && state.live && state.audio;
      if (!available) stop('Audio is offline.');
      else if (button.disabled) status.textContent = '';
      button.disabled = !available;
    } catch (_) {
      available = false;
      stop('Audio is offline.');
      button.disabled = true;
    }
  }
  check();
  const timer = setInterval(check, 5000);
  window.addEventListener('pagehide', () => { clearInterval(timer); stop(); });
})();

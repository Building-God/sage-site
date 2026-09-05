// Existing Tailscale address now fronts the restricted read-only board gateway.
const BOARD_ORIGIN = "https://grunty.tail197337.ts.net";
const frame = document.querySelector('#board');
const offline = document.querySelector('#offline');
const status = document.querySelector('#status');
const retry = document.querySelector('#retry');
function showOffline() {
  frame.hidden = true;
  frame.removeAttribute('src');
  offline.hidden = false;
  status.classList.remove('live');
  status.lastChild.textContent = 'Offline';
}
async function checkBoard() {
  retry.disabled = true;
  try {
    if (!BOARD_ORIGIN) { showOffline(); return; }
    const response = await fetch(BOARD_ORIGIN + '/status', {cache: 'no-store', credentials: 'omit', signal: AbortSignal.timeout(5000)});
    const data = response.ok ? await response.json() : null;
    if (data?.live !== true) { showOffline(); return; }
    if (!frame.hasAttribute('src')) frame.src = BOARD_ORIGIN + '/?skin=broadcast';
    frame.hidden = false;
    offline.hidden = true;
    status.classList.add('live');
    status.lastChild.textContent = 'Live';
  } catch { showOffline(); }
  finally { retry.disabled = false; }
}
retry.addEventListener('click', checkBoard);
checkBoard();
setInterval(checkBoard, 15000);

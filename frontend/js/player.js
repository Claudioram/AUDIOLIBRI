/**
 * Sticky bottom player that persists playback across chapter switches.
 */
const playerBar  = document.getElementById('playerBar');
const stickyAudio = document.getElementById('stickyAudio');
const nowPlaying  = document.getElementById('nowPlaying');

let _currentChapterId = null;

function playChapter(chapterId, title) {
  _currentChapterId = chapterId;
  stickyAudio.src = `/api/chapters/${chapterId}/audio`;
  nowPlaying.textContent = title;
  playerBar.classList.add('visible');
  stickyAudio.play();
}

function stopPlayer() {
  stickyAudio.pause();
  playerBar.classList.remove('visible');
  _currentChapterId = null;
}

stickyAudio.addEventListener('ended', () => {
  // Could auto-advance to next chapter here in future
  playerBar.classList.remove('visible');
});

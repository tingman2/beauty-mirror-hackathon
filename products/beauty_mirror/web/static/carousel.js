(() => {
  'use strict';
  document.querySelectorAll('.beauty-carousel-section').forEach(section => {
  const ring = section.querySelector('.beauty-ring');
  const opener = section.querySelector('.beauty-explore');
  const dialog = document.getElementById(section.dataset.preview);
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  let frame = 0;
  let timer = 0;
  let opening = false;
  function render() {
    frame = 0;
    const box = section.getBoundingClientRect();
    const progress = Math.max(0, Math.min(1, (innerHeight - box.top) / (innerHeight + box.height)));
    const mobile = innerWidth <= 700;
    section.style.setProperty('--ring-radius', mobile ? '300px' : '460px');
    section.style.setProperty('--beauty-progress', progress.toFixed(4));
    section.style.setProperty('--card-tilt', reduced.matches ? '0deg' : `${8 - progress * 16}deg`);
    if (!opening) ring.style.transform = `translateZ(${mobile ? -340 : -480}px) rotateX(${reduced.matches ? 0 : 3 - progress * 6}deg) rotateY(${reduced.matches ? -60 : (section.classList.contains('beauty-carousel-next') ? progress * 240 - 60 : -progress * 240)}deg) rotateZ(${reduced.matches ? 0 : 3 - progress * 6}deg)`;
  }
  function schedule() { if (!frame) frame = requestAnimationFrame(render); }
  addEventListener('scroll', schedule, { passive: true });
  addEventListener('resize', schedule);
  reduced.addEventListener('change', schedule);
  render();
  const cells = [...section.querySelectorAll('.beauty-cell')];
  let lastCard = null;
  const caption = opener.querySelector('.beauty-explore-caption');
  const originalCaption = caption.innerHTML;
  opener.setAttribute('aria-haspopup', 'dialog');
  ring.id = section.id + '-cards';
  caption.innerHTML = '继续下滑，进入下一组 · 点击图片查看 <b>↓</b>';
  section.classList.add('scroll-interactive');
  opener.addEventListener('click', () => exploreCard(cells[0]));
  function exploreCard(cell) {
    if (opening || dialog.open) return;
    lastCard = cell;
    opening = true;
    section.classList.add('is-expanding');
    timer = setTimeout(() => {
      dialog.showModal();
      document.body.classList.add('beauty-dialog-open');
      dialog.classList.add('is-open');
      const title = cell.querySelector('.beauty-card-face span').textContent;
      const selected = [...dialog.querySelectorAll('.beauty-grid-item')].find(item => item.textContent.includes(title));
      dialog.querySelectorAll('.beauty-grid-item').forEach(item => item.classList.toggle('is-selected', item === selected));
      (selected || dialog.querySelector('.beauty-preview-close')).focus();
    }, reduced.matches ? 0 : 450);
  }
  cells.forEach(cell => {
    cell.setAttribute('role', 'button');
    cell.setAttribute('aria-haspopup', 'dialog');
    cell.setAttribute('aria-label', '探索' + cell.querySelector('.beauty-card-face span').textContent);
    cell.tabIndex = 0;
    cell.addEventListener('click', () => exploreCard(cell));
    cell.addEventListener('keydown', event => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); exploreCard(cell); }
    });
  });
  function close() { if (dialog.open) dialog.close(); }
  dialog.querySelector('.beauty-preview-close').addEventListener('click', close);
  dialog.addEventListener('click', event => { if (event.target === dialog) close(); });
  dialog.addEventListener('close', () => {
    clearTimeout(timer);
    opening = false;
    dialog.classList.remove('is-open');
    document.body.classList.remove('beauty-dialog-open');
    section.classList.remove('is-expanding');
    render();
    (lastCard || opener).focus({ preventScroll: true });
  });
  });
})();

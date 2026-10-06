(() => {
  'use strict';
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  const gallery = document.querySelector('.orbit-gallery');
  const vision = document.querySelector('.vision-panel');
  if (!gallery || !vision) return;
  const targets = document.querySelectorAll('.future-gallery h2, .vision-panel, .ritual-layout, .about-notes');
  const observer = new IntersectionObserver(entries => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.classList.add('is-revealed');
        observer.unobserve(entry.target);
      }
    });
  }, { threshold: 0.12 });
  targets.forEach(target => { target.classList.add('reveal-ready'); observer.observe(target); });
  let scheduled = false;
  function renderScroll() {
    scheduled = false;
    if (reduced.matches) return;
    const box = gallery.getBoundingClientRect();
    const progress = Math.max(0, Math.min(1, (innerHeight - box.top) / (innerHeight + box.height)));
    gallery.style.setProperty('--gallery-progress', progress.toFixed(3));
    const hero = document.querySelector('.future-stage');
    hero.style.setProperty('--hero-shift', Math.min(30, window.scrollY * 0.06) + 'px');
  }
  window.addEventListener('scroll', () => {
    if (!scheduled) { scheduled = true; requestAnimationFrame(renderScroll); }
  }, { passive: true });
  renderScroll();
  const heroSection = document.querySelector('.future-hero');
  if (heroSection) {
    let pointerFrame = null;
    heroSection.addEventListener('pointermove', event => {
      if (reduced.matches || event.pointerType === 'touch') return;
      if (pointerFrame) cancelAnimationFrame(pointerFrame);
      pointerFrame = requestAnimationFrame(() => {
        const box = heroSection.getBoundingClientRect();
        heroSection.style.setProperty('--portrait-x', ((event.clientX - box.left) / box.width - 0.5) * 12 + 'px');
        heroSection.style.setProperty('--portrait-y', ((event.clientY - box.top) / box.height - 0.5) * 8 + 'px');
        pointerFrame = null;
      });
    });
    heroSection.addEventListener('pointerleave', () => {
      if (pointerFrame) cancelAnimationFrame(pointerFrame);
      heroSection.style.setProperty('--portrait-x', '0px');
      heroSection.style.setProperty('--portrait-y', '0px');
    });
  }
  vision.addEventListener('pointermove', event => {
    if (reduced.matches || event.pointerType === 'touch') return;
    const box = vision.getBoundingClientRect();
    vision.style.setProperty('--light-x', (event.clientX - box.left) / box.width * 100 + '%');
    vision.style.setProperty('--light-y', (event.clientY - box.top) / box.height * 100 + '%');
  });
})();

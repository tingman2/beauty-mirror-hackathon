(() => {
  'use strict';
  const $ = selector => document.querySelector(selector);
  const esc = text => String(text ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const safeLink = value => { try { const url = new URL(value); return url.protocol === 'https:' ? url.href : ''; } catch { return ''; } };
  const image = value => { const url = safeLink(value); return url ? '/api/img?url=' + encodeURIComponent(url) : ''; };
  let catalog = [], cards = [];
  const styleId = new URLSearchParams(location.search).get('style') || 'all';
  async function load() {
    $('#category-retry').classList.add('hidden');
    try {
      const response = await fetch('/static/categories-data.json');
      if (!response.ok) throw new Error('分类内容暂时无法加载');
      catalog = await response.json();
      const selected = catalog.find(style => style.id === styleId);
      const name = selected ? selected.name : '全部妆教';
      document.title = '妆镜 · ' + name + '博主专区';
      $('#category-title').textContent = name + ' · 博主专区';
      $('#category-description').textContent = selected ? selected.keywords.join(' / ') + '，从喜欢的风格开始练习。' : '浏览五种妆教风格，找到喜欢的博主与作品。';
      $('#category-nav').innerHTML = [{id:'all',name:'全部妆教'},...catalog].map(style => `<a class="${style.id === (selected ? selected.id : 'all') ? 'active' : ''}" href="/static/category.html?style=${encodeURIComponent(style.id)}">${esc(style.name)}</a>`).join('');
      const sections = selected ? [selected] : catalog;
      const tutorials = sections.flatMap(style => style.tutorials.map(tutorial => ({...tutorial,style:style.name})));
      $('#tutorial-list').innerHTML = tutorials.map((tutorial,index) => `<a class="tutorial-entry" href="/static/workbench.html"><span>${String(index+1).padStart(2,'0')} / ${esc(tutorial.style)}</span><h3>${esc(tutorial.title)}</h3><p>${tutorial.focus.map(esc).join(' · ')} <b>↗</b></p></a>`).join('');
      const unique = new Map();
      sections.flatMap(style => style.cards).forEach(card => unique.set(card.id,card));
      cards = [...unique.values()];
      render();
    } catch (error) {
      $('#creator-list').innerHTML = '<p class="empty">分类内容暂时无法加载，请重试。</p>';
      $('#category-retry').classList.remove('hidden');
    }
  }
  function render() {
    const search = $('#creator-search').value.trim().toLowerCase();
    const platform = $('#platform-filter').value;
    const filtered = cards.filter(card => (platform === 'all' || card.platform === platform) && (card.creator + ' ' + (card.style_tags || []).join(' ')).toLowerCase().includes(search));
    const grouped = new Map();
    filtered.forEach(card => {
      const key = card.platform + ':' + card.creator;
      if (!grouped.has(key)) grouped.set(key,{...card,works:[]});
      grouped.get(key).works.push(card);
    });
    $('#creator-count').textContent = `${grouped.size} 位博主 · ${filtered.length} 个作品`;
    $('#creator-list').innerHTML = grouped.size ? [...grouped.values()].map(creator => {
      const profile = safeLink(creator.creator_profile);
      return `<article class="category-creator"><div class="category-creator-head">${creator.creator_avatar ? `<img src="${esc(image(creator.creator_avatar))}" alt="" loading="lazy">` : '<span class="creator-monogram">✳</span>'}<div><h3>${esc(creator.creator)}</h3><small>${creator.platform === 'douyin' ? '抖音' : '小红书'} · ${creator.works.length} 个作品</small></div>${profile ? `<a href="${esc(profile)}" target="_blank" rel="noopener noreferrer">博主主页 ↗</a>` : ''}</div><div class="category-works">${creator.works.map(work => {const url=safeLink(work.original_url); return url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer"><img src="${esc(image(work.cover_url))}" alt="${esc(work.creator)}的妆教风格参考" loading="lazy"><span>${esc((work.style_tags || []).slice(0,3).join(' / '))}<b>看原作品 ↗</b></span></a>` : '';}).join('')}</div></article>`;
    }).join('') : '<div class="empty">暂无匹配博主，试试其他关键词或平台。</div>';
    $('#creator-list').querySelectorAll('img').forEach(img => img.addEventListener('error', () => { img.classList.add('image-unavailable'); img.alt='封面暂时不可用，可点击查看原作品'; },{once:true}));
  }
  $('#creator-search').addEventListener('input',render);
  $('#platform-filter').addEventListener('change',render);
  $('#category-retry').addEventListener('click',load);
  load();
})();

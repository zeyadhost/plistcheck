// Keep the native selects as the source of form values and hardware availability.
(() => {
  const chevron = '<svg class="chevron" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="m4 6 4 4 4-4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const check = '<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="m3 8 3 3 7-7" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  let opened = null;
  const controls = [];

  for (const select of document.querySelectorAll('#profile select')) {
    const label = select.closest('label');
    const caption = document.createElement('span');
    caption.id = `${select.name}-label`;
    caption.textContent = label.firstChild.textContent.trim();
    label.firstChild.replaceWith(caption);
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'select-trigger';
    button.id = `${select.name}-control`;
    button.setAttribute('role', 'combobox');
    button.setAttribute('aria-labelledby', caption.id);
    button.setAttribute('aria-haspopup', 'listbox');
    button.setAttribute('aria-expanded', 'false');
    if (select.hasAttribute('aria-describedby')) button.setAttribute('aria-describedby', select.getAttribute('aria-describedby'));
    button.innerHTML = `<span class="select-value"></span>${chevron}`;
    label.htmlFor = button.id;
    select.hidden = true;
    select.after(button);

    // A body-level menu escapes the inspector window's rounded clipping edge.
    const menu = document.createElement('div');
    menu.className = 'select-menu';
    menu.id = `${select.name}-options`;
    menu.setAttribute('role', 'listbox');
    menu.setAttribute('aria-labelledby', caption.id);
    menu.hidden = true;
    document.body.append(menu);
    button.setAttribute('aria-controls', menu.id);
    let options = [];
    let active = -1;
    let search = '';
    let typedAt = 0;

    const close = () => {
      menu.hidden = true;
      button.setAttribute('aria-expanded', 'false');
      button.removeAttribute('aria-activedescendant');
      if (opened === control) opened = null;
    };
    const sync = () => {
      button.disabled = select.disabled;
      button.querySelector('.select-value').textContent = select.selectedOptions[0]?.textContent || '';
      button.title = button.querySelector('.select-value').textContent;
      if (select.disabled || !button.getClientRects().length) close();
    };
    const highlight = (index) => {
      active = Math.max(0, Math.min(index, options.length - 1));
      for (const [i, option] of [...menu.querySelectorAll('[role="option"]')].entries()) {
        option.classList.toggle('active', i === active);
        if (i === active) {
          button.setAttribute('aria-activedescendant', option.id);
          option.scrollIntoView({block: 'nearest'});
        }
      }
    };
    const choose = (restoreFocus = true) => {
      const value = options[active]?.value;
      const changed = value !== undefined && value !== select.value;
      if (changed) select.value = value;
      close();
      sync();
      if (changed) select.dispatchEvent(new Event('change', {bubbles: true}));
      if (restoreFocus) button.focus({preventScroll: true});
    };
    const open = () => {
      if (button.disabled) return;
      if (opened) opened.close();
      menu.replaceChildren();
      options = [...select.options].filter(option => !option.hidden && !option.disabled && !option.parentElement.disabled);
      let group = null;
      let container = menu;
      for (const [index, option] of options.entries()) {
        const parent = option.parentElement;
        if (parent !== group) {
          group = parent;
          container = menu;
          if (parent.tagName === 'OPTGROUP') {
            container = document.createElement('div');
            container.setAttribute('role', 'group');
            container.setAttribute('aria-label', parent.label);
            const heading = document.createElement('div');
            heading.className = 'select-group-label';
            heading.setAttribute('aria-hidden', 'true');
            heading.textContent = parent.label;
            container.append(heading);
            menu.append(container);
          }
        }
        const row = document.createElement('div');
        row.id = `${select.name}-option-${index}`;
        row.setAttribute('role', 'option');
        row.setAttribute('aria-selected', String(option.selected));
        row.dataset.index = index;
        const text = document.createElement('span');
        text.textContent = option.textContent;
        row.innerHTML = check;
        row.append(text);
        container.append(row);
      }
      const rect = button.getBoundingClientRect();
      const viewport = window.visualViewport;
      const viewportTop = viewport?.offsetTop || 0;
      const viewportLeft = viewport?.offsetLeft || 0;
      const viewportWidth = viewport?.width || innerWidth;
      const viewportBottom = viewportTop + (viewport?.height || innerHeight);
      const below = viewportBottom - rect.bottom - 12;
      const above = rect.top - viewportTop - 12;
      const upward = below < Math.min(240, above);
      const height = Math.min(320, Math.max(44, upward ? above : below));
      const width = Math.min(Math.max(rect.width, 260), viewportWidth - 24);
      menu.style.width = `${width}px`;
      menu.style.maxHeight = `${height}px`;
      menu.style.left = `${Math.max(viewportLeft + 12, Math.min(rect.left, viewportLeft + viewportWidth - width - 12))}px`;
      menu.style.top = upward ? 'auto' : `${rect.bottom + 5}px`;
      menu.style.bottom = upward ? `${innerHeight - rect.top + 5}px` : 'auto';
      menu.hidden = false;
      button.setAttribute('aria-expanded', 'true');
      opened = control;
      search = '';
      highlight(Math.max(0, options.findIndex(option => option.selected)));
    };
    const control = {button, menu, close, sync};
    controls.push(control);
    sync();
    button.addEventListener('click', () => menu.hidden ? open() : close());
    menu.addEventListener('mousedown', event => event.preventDefault());
    menu.addEventListener('click', event => {
      const row = event.target.closest('[role="option"]');
      if (!row) return;
      active = Number(row.dataset.index);
      choose();
    });
    menu.addEventListener('pointermove', event => {
      if (event.pointerType !== 'mouse') return;
      const row = event.target.closest('[role="option"]');
      if (row) highlight(Number(row.dataset.index));
    });
    button.addEventListener('keydown', event => {
      if (event.key === 'Escape') {
        if (!menu.hidden) { event.preventDefault(); event.stopPropagation(); close(); }
        return;
      }
      if (event.key === 'Tab') { if (!menu.hidden) choose(false); return; }
      if (event.key === 'Enter' || (event.key === ' ' && (!search || Date.now() - typedAt > 600))) {
        event.preventDefault();
        if (menu.hidden) open(); else choose();
        return;
      }
      const moves = {ArrowDown: 1, ArrowUp: -1, PageDown: 8, PageUp: -8};
      if (Object.hasOwn(moves, event.key) || ['Home', 'End'].includes(event.key)) {
        event.preventDefault();
        const wasClosed = menu.hidden;
        if (wasClosed) open();
        if (event.key === 'Home') highlight(0);
        else if (event.key === 'End') highlight(options.length - 1);
        else if (!wasClosed) highlight(active + moves[event.key]);
        return;
      }
      if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
        event.preventDefault();
        if (menu.hidden) open();
        const now = Date.now();
        search = now - typedAt > 600 ? event.key.toLowerCase() : search + event.key.toLowerCase();
        typedAt = now;
        const repeated = [...search].every(char => char === search[0]);
        const query = repeated ? search[0] : search;
        const start = repeated ? active + 1 : active;
        for (let step = 0; step < options.length; step += 1) {
          const index = (start + step) % options.length;
          if (options[index].textContent.toLowerCase().startsWith(query)) { highlight(index); break; }
        }
      }
    });
    new MutationObserver(sync).observe(select, {attributes: true, subtree: true, attributeFilter: ['disabled', 'hidden', 'selected']});
  }
  document.addEventListener('change', () => controls.forEach(control => control.sync()));
  document.addEventListener('pointerdown', event => {
    if (opened && !opened.button.contains(event.target) && !opened.menu.contains(event.target)) opened.close();
  });
  document.addEventListener('focusin', event => {
    if (opened && event.target !== opened.button && !opened.menu.contains(event.target)) opened.close();
  });
  document.addEventListener('scroll', event => {
    if (opened && !opened.menu.contains(event.target)) opened.close();
  }, true);
  window.addEventListener('resize', () => opened?.close());
})();

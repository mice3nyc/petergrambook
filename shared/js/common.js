/*
 * common.js
 * Shared utilities for Petergram & Peterbook
 * 1984 Macintosh-style local SNS archive viewer
 */

/* ============================================
   TAB SWITCHING
   ============================================ */

/**
 * Initialize tab navigation within a container.
 * Tabs should have [data-tab] attribute, panels should have [data-panel].
 * @param {string} containerSelector - CSS selector for the tab container
 */
function initTabs(containerSelector) {
  const container = document.querySelector(containerSelector);
  if (!container) return;

  const tabs = container.querySelectorAll('.mac-tab');
  const window = container.closest('.mac-window') || container.parentElement;
  const panels = window.querySelectorAll('.mac-tab-panel');

  tabs.forEach(tab => {
    tab.addEventListener('click', () => {
      const targetPanel = tab.getAttribute('data-tab');

      // Deactivate all tabs and panels
      tabs.forEach(t => t.classList.remove('active'));
      panels.forEach(p => p.classList.remove('active'));

      // Activate clicked tab and corresponding panel
      tab.classList.add('active');
      const panel = window.querySelector(`[data-panel="${targetPanel}"]`);
      if (panel) panel.classList.add('active');
    });
  });
}

/* ============================================
   WINDOW CONTROLS
   ============================================ */

/**
 * Initialize close button behavior for Mac-style windows.
 * Click the close box to animate and hide the window.
 */
function initWindowControls() {
  document.querySelectorAll('.mac-window-close').forEach(closeBtn => {
    closeBtn.addEventListener('click', (e) => {
      const win = closeBtn.closest('.mac-window');
      if (win) {
        win.classList.add('animate-close');
        win.addEventListener('animationend', () => {
          win.style.display = 'none';
        }, { once: true });
      }
    });
  });
}

/**
 * Show a window that was previously closed.
 * @param {string} selector - CSS selector for the window
 */
function showWindow(selector) {
  const win = document.querySelector(selector);
  if (win) {
    win.style.display = '';
    win.classList.remove('animate-close');
    win.classList.add('animate-open');
    win.addEventListener('animationend', () => {
      win.classList.remove('animate-open');
    }, { once: true });
  }
}

/* ============================================
   VIEW TOGGLE (Grid / List)
   ============================================ */

/**
 * Initialize grid/list view toggle.
 * @param {string} toggleSelector - selector for the toggle container
 * @param {string} gridSelector - selector for the post grid
 */
function initViewToggle(toggleSelector, gridSelector) {
  const toggle = document.querySelector(toggleSelector);
  const grid = document.querySelector(gridSelector);
  if (!toggle || !grid) return;

  toggle.querySelectorAll('.view-toggle-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      toggle.querySelectorAll('.view-toggle-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');

      const view = btn.getAttribute('data-view');
      if (view === 'list') {
        grid.classList.add('list-view');
      } else {
        grid.classList.remove('list-view');
      }
    });
  });
}

/* ============================================
   DATE FORMATTING
   ============================================ */

/**
 * Format a date string or timestamp for display.
 * @param {string|number} dateInput - ISO string, timestamp, or date string
 * @param {string} format - 'short' | 'long' | 'relative'
 * @returns {string} Formatted date string
 */
function formatDate(dateInput, format = 'short') {
  const date = new Date(dateInput);
  if (isNaN(date.getTime())) return '—';

  if (format === 'relative') {
    return getRelativeDate(date);
  }

  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');

  if (format === 'long') {
    const hours = String(date.getHours()).padStart(2, '0');
    const mins = String(date.getMinutes()).padStart(2, '0');
    return `${year}-${month}-${day} ${hours}:${mins}`;
  }

  return `${year}-${month}-${day}`;
}

/**
 * Get relative time string (e.g., "3일 전", "2시간 전")
 * @param {Date} date
 * @returns {string}
 */
function getRelativeDate(date) {
  const now = new Date();
  const diffMs = now - date;
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);

  if (diffMins < 1) return '방금 전';
  if (diffMins < 60) return `${diffMins}분 전`;
  if (diffHours < 24) return `${diffHours}시간 전`;
  if (diffDays < 30) return `${diffDays}일 전`;
  if (diffDays < 365) return `${Math.floor(diffDays / 30)}개월 전`;
  return `${Math.floor(diffDays / 365)}년 전`;
}

/* ============================================
   PAGINATION
   ============================================ */

/**
 * Simple pagination helper.
 * @param {Array} items - Full array of items
 * @param {number} page - Current page (1-based)
 * @param {number} perPage - Items per page
 * @returns {{ items: Array, page: number, totalPages: number, hasNext: boolean, hasPrev: boolean }}
 */
function paginate(items, page = 1, perPage = 30) {
  const totalPages = Math.ceil(items.length / perPage);
  page = Math.max(1, Math.min(page, totalPages));
  const start = (page - 1) * perPage;
  const end = start + perPage;

  return {
    items: items.slice(start, end),
    page,
    totalPages,
    total: items.length,
    hasNext: page < totalPages,
    hasPrev: page > 1
  };
}

/**
 * Render pagination controls.
 * @param {HTMLElement} container - Element to render controls into
 * @param {{ page: number, totalPages: number, total: number }} pageInfo
 * @param {function} onPageChange - Callback when page changes
 */
function renderPagination(container, pageInfo, onPageChange) {
  container.innerHTML = '';
  if (pageInfo.totalPages <= 1) return;

  const wrapper = document.createElement('div');
  wrapper.style.cssText = 'display:flex; justify-content:center; align-items:center; gap:8px; padding:12px; font-size:13px;';

  const prevBtn = document.createElement('button');
  prevBtn.className = 'mac-button';
  prevBtn.textContent = '< Prev';
  prevBtn.disabled = !pageInfo.hasPrev;
  prevBtn.style.opacity = pageInfo.hasPrev ? '1' : '0.4';
  prevBtn.addEventListener('click', () => onPageChange(pageInfo.page - 1));

  const info = document.createElement('span');
  info.textContent = `Page ${pageInfo.page} of ${pageInfo.totalPages} (${pageInfo.total} items)`;

  const nextBtn = document.createElement('button');
  nextBtn.className = 'mac-button';
  nextBtn.textContent = 'Next >';
  nextBtn.disabled = !pageInfo.hasNext;
  nextBtn.style.opacity = pageInfo.hasNext ? '1' : '0.4';
  nextBtn.addEventListener('click', () => onPageChange(pageInfo.page + 1));

  wrapper.appendChild(prevBtn);
  wrapper.appendChild(info);
  wrapper.appendChild(nextBtn);
  container.appendChild(wrapper);
}

/* ============================================
   DATABASE LOADING (sql.js placeholder)
   ============================================ */

/**
 * Load a SQLite database using sql.js.
 * This is a placeholder — sql.js must be included separately.
 * @param {string} dbPath - Path to the .db file
 * @returns {Promise<object|null>} Database instance or null
 */
async function loadDatabase(dbPath) {
  // Check if sql.js is available
  if (typeof initSqlJs === 'undefined') {
    console.warn('[Petergram/Peterbook] sql.js not loaded. Using placeholder data.');
    return null;
  }

  try {
    const SQL = await initSqlJs();
    const response = await fetch(dbPath);
    const buffer = await response.arrayBuffer();
    const db = new SQL.Database(new Uint8Array(buffer));
    console.log(`[DB] Loaded: ${dbPath}`);
    return db;
  } catch (err) {
    console.error(`[DB] Failed to load ${dbPath}:`, err);
    return null;
  }
}

/**
 * Execute a query on a database and return results as an array of objects.
 * @param {object} db - sql.js database instance
 * @param {string} query - SQL query string
 * @param {Array} params - Query parameters
 * @returns {Array<object>}
 */
function dbQuery(db, query, params = []) {
  if (!db) return [];

  try {
    const stmt = db.prepare(query);
    if (params.length) stmt.bind(params);

    const results = [];
    while (stmt.step()) {
      results.push(stmt.getAsObject());
    }
    stmt.free();
    return results;
  } catch (err) {
    console.error('[DB] Query error:', err);
    return [];
  }
}

/* ============================================
   MENU BAR CLOCK
   ============================================ */

/**
 * Update the menu bar clock display.
 */
function updateClock() {
  const clockEl = document.querySelector('.menu-clock');
  if (!clockEl) return;

  const now = new Date();
  const hours = String(now.getHours()).padStart(2, '0');
  const mins = String(now.getMinutes()).padStart(2, '0');
  const day = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][now.getDay()];
  clockEl.textContent = `${day} ${hours}:${mins}`;
}

/* ============================================
   SEARCH HELPER
   ============================================ */

/**
 * Simple client-side search across text fields.
 * @param {Array<object>} items - Array of objects to search
 * @param {string} query - Search query
 * @param {Array<string>} fields - Object keys to search in
 * @returns {Array<object>} Matching items
 */
function searchItems(items, query, fields) {
  if (!query || !query.trim()) return items;

  const q = query.toLowerCase().trim();
  return items.filter(item => {
    return fields.some(field => {
      const val = item[field];
      return val && String(val).toLowerCase().includes(q);
    });
  });
}

/* ============================================
   TEXT HELPERS
   ============================================ */

/**
 * Truncate text to a maximum length with ellipsis.
 * @param {string} text
 * @param {number} maxLength
 * @returns {string}
 */
function truncateText(text, maxLength = 100) {
  if (!text) return '';
  if (text.length <= maxLength) return text;
  return text.substring(0, maxLength) + '...';
}

/**
 * Escape HTML special characters.
 * @param {string} str
 * @returns {string}
 */
function escapeHtml(str) {
  if (!str) return '';
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

/* ============================================
   INITIALIZATION
   ============================================ */

/**
 * Common initialization — call on every page.
 */
function initCommon() {
  // Start clock
  updateClock();
  setInterval(updateClock, 30000);

  // Initialize window close buttons
  initWindowControls();

  // Open animation for windows on load
  document.querySelectorAll('.mac-window').forEach(win => {
    win.classList.add('animate-open');
    win.addEventListener('animationend', () => {
      win.classList.remove('animate-open');
    }, { once: true });
  });
}

/* ============================================
   CLASSIC MAC DITHERING PATTERNS
   ============================================ */

/**
 * Get a dithering pattern class for chart bars.
 * Cycles through patterns to differentiate data series.
 * @param {number} index - Index of the bar/series
 * @returns {string} CSS class name
 */
const DITHER_PATTERNS = [
  'dither-solid',
  'dither-diagonal',
  'dither-crosshatch',
  'dither-horizontal',
  'dither-dots',
  'dither-checker',
  'dither-vertical',
  'dither-diagonal-rev',
  'dither-dots-sparse',
  'dither-brick',
];

function getDitherClass(index) {
  return DITHER_PATTERNS[index % DITHER_PATTERNS.length];
}

/* ============================================
   IMAGE CAROUSEL
   ============================================ */

/**
 * Initialize all carousels on the page.
 * Each carousel container should have class 'mac-card-carousel'
 * with a .carousel-track containing .carousel-slide elements.
 */
function initCarousels() {
  document.querySelectorAll('.mac-card-carousel').forEach(carousel => {
    const track = carousel.querySelector('.carousel-track');
    const slides = carousel.querySelectorAll('.carousel-slide');
    const dots = carousel.querySelectorAll('.carousel-dot');
    const prevBtn = carousel.querySelector('.carousel-btn.prev');
    const nextBtn = carousel.querySelector('.carousel-btn.next');

    if (slides.length <= 1) {
      // Hide controls for single image
      if (prevBtn) prevBtn.style.display = 'none';
      if (nextBtn) nextBtn.style.display = 'none';
      if (carousel.querySelector('.carousel-dots')) {
        carousel.querySelector('.carousel-dots').style.display = 'none';
      }
      return;
    }

    let current = 0;

    function goTo(index) {
      current = Math.max(0, Math.min(index, slides.length - 1));
      track.style.transform = `translateX(-${current * 100}%)`;
      dots.forEach((dot, i) => dot.classList.toggle('active', i === current));
    }

    if (prevBtn) prevBtn.addEventListener('click', () => goTo(current - 1));
    if (nextBtn) nextBtn.addEventListener('click', () => goTo(current + 1));
    dots.forEach((dot, i) => dot.addEventListener('click', () => goTo(i)));

    // Keyboard support
    carousel.setAttribute('tabindex', '0');
    carousel.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowLeft') goTo(current - 1);
      if (e.key === 'ArrowRight') goTo(current + 1);
    });
  });
}

/**
 * Create carousel HTML for a post with multiple images.
 * @param {Array<string>} imagePaths - Array of image file paths
 * @returns {string} HTML string for the carousel
 */
function isVideo(path) {
  return /\.(mp4|mov|avi|webm|m4v)$/i.test(path);
}

function mediaTag(path) {
  const src = escapeHtml(path);
  if (isVideo(path)) {
    return `<video src="${src}" controls preload="metadata" style="width:100%;height:100%;object-fit:cover;"></video>`;
  }
  return `<img src="${src}" alt="post image" loading="lazy">`;
}

function createCarouselHtml(imagePaths, extraClass = '') {
  if (!imagePaths || imagePaths.length === 0) {
    return `<div class="mac-card-image${extraClass}"><span class="placeholder-icon">□</span></div>`;
  }

  if (imagePaths.length === 1) {
    return `<div class="mac-card-image${extraClass}">${mediaTag(imagePaths[0])}</div>`;
  }

  const slides = imagePaths.map(p =>
    `<div class="carousel-slide">${mediaTag(p)}</div>`
  ).join('');

  const dots = imagePaths.map((_, i) =>
    `<div class="carousel-dot${i === 0 ? ' active' : ''}" data-index="${i}"></div>`
  ).join('');

  return `
    <div class="mac-card-carousel${extraClass}">
      <div class="carousel-track">${slides}</div>
      <button class="carousel-btn prev">◀</button>
      <button class="carousel-btn next">▶</button>
      <div class="carousel-dots">${dots}</div>
    </div>`;
}

/**
 * Create caption HTML with scroll box for long text.
 * Shows full text up to 15 lines, then scrolls.
 * @param {string} caption - Caption text
 * @returns {string} HTML string
 */
function createCaptionHtml(caption) {
  if (!caption) return '';
  return `<div class="mac-card-caption-box">${escapeHtml(caption)}</div>`;
}

// Auto-init when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
  initCommon();
  initCarousels();
});

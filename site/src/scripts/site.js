// Lhassa presence site: the entire site's client JS. Two small, calm
// behaviors: scroll reveals and the language toggle. No framework, no build
// step needed beyond Astro copying this file as-is -- same spirit as
// witara.site's scripts/site.js (this file is a direct sibling, minus the
// rotating word and touch-gallery pieces this single-page site doesn't use).

// ----- Reveals: fade elements in when they enter the viewport, and reset
// when they leave, so scrolling back up re-animates them. CSS owns the
// transition (gated behind html.js, set by the head's inline langInit
// script) and prefers-reduced-motion disables it entirely there. -----
;(function reveals() {
  var els = Array.prototype.slice.call(document.querySelectorAll('[data-reveal]'))
  if (!els.length) return
  if (!('IntersectionObserver' in window)) {
    els.forEach(function (el) {
      el.classList.add('in')
    })
    return
  }
  var io = new IntersectionObserver(
    function (entries) {
      entries.forEach(function (entry) {
        entry.target.classList.toggle('in', entry.isIntersecting)
      })
    },
    { rootMargin: '0px 0px -60px 0px', threshold: 0.05 },
  )
  els.forEach(function (el) {
    io.observe(el)
  })
})()

// ----- Language toggle: delegated click on any .lang-toggle button. -----
// Also keeps every .lang-toggle button's accessible name in sync with what
// it will actually do next, and announces the change via an aria-live
// region (#lang-announce, in Base.astro); a silent html[data-lang] flip
// is otherwise invisible to screen readers, which never re-read the page.
function updateLangToggleLabels() {
  var isEs = document.documentElement.dataset.lang === 'es'
  var label = isEs ? 'Cambiar a inglés / Switch to English' : 'Cambiar a español / Switch to Spanish'
  var buttons = document.querySelectorAll('.lang-toggle')
  for (var i = 0; i < buttons.length; i++) {
    buttons[i].setAttribute('aria-label', label)
  }
}
updateLangToggleLabels()

document.addEventListener('click', function (e) {
  var btn = e.target.closest('.lang-toggle')
  if (!btn) return
  var html = document.documentElement
  var next = html.dataset.lang === 'es' ? 'en' : 'es'
  html.dataset.lang = next
  var t = html.dataset[next === 'en' ? 'titleEn' : 'titleEs']
  if (t) document.title = t
  html.lang = next
  try {
    localStorage.setItem('lhassa_lang', next)
  } catch (_) {}
  updateLangToggleLabels()
  var announce = document.getElementById('lang-announce')
  if (announce) {
    announce.textContent = next === 'es' ? 'Idioma: español' : 'Language: English'
  }
})

// ----- Touch gallery toggle: the noche->color photo swap on hover (CSS
// figure:hover) doesn't exist on touch devices. On devices with no hover
// mechanism, a tap on a figure toggles .show-color; a second tap on the same
// figure removes it. Untouched on devices that do have hover. -----
;(function touchGallery() {
  if (!window.matchMedia('(hover: none)').matches) return
  document.addEventListener('click', function (e) {
    var figure = e.target.closest('figure')
    if (!figure) return
    figure.classList.toggle('show-color')
  })
})()

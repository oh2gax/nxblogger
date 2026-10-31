// Simple image lightbox for blog posts.
(function () {
  var box = document.getElementById('lightbox');
  if (!box) return;
  var big = box.querySelector('img');
  document.querySelectorAll('.post-content img').forEach(function (img) {
    if (img.closest('a')) return; // linked images keep their link
    img.addEventListener('click', function () {
      big.src = img.currentSrc || img.src;
      box.hidden = false;
    });
  });
  box.addEventListener('click', function () { box.hidden = true; big.src = ''; });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') box.hidden = true; });
})();

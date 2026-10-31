// NXBlogger post editor: rich text with image upload by button, paste and drag & drop.
(function () {
  'use strict';
  var form = document.getElementById('post-form');
  if (!form) return;
  var editor = document.getElementById('editor');
  var source = document.getElementById('source');
  var input = document.getElementById('content-input');
  var toolbar = document.getElementById('toolbar');
  var fileInput = document.getElementById('image-input');
  var statusEl = document.getElementById('editor-status');
  var uploadUrl = form.dataset.uploadUrl;
  var csrf = form.dataset.csrf;

  var sourceMode = false, dirty = false, pending = 0, savedRange = null, statusTimer = null;

  editor.innerHTML = input.value;
  try { document.execCommand('defaultParagraphSeparator', false, 'p'); } catch (e) { /* old browser */ }

  function setStatus(msg, sticky) {
    statusEl.textContent = msg || '';
    clearTimeout(statusTimer);
    if (msg && !sticky) statusTimer = setTimeout(function () { statusEl.textContent = ''; }, 5000);
  }

  function markDirty() { dirty = true; }

  // --- Selection handling ---------------------------------------------------
  function saveSelection() {
    var sel = window.getSelection();
    if (sel.rangeCount && editor.contains(sel.getRangeAt(0).commonAncestorContainer)) {
      savedRange = sel.getRangeAt(0).cloneRange();
    }
  }

  function restoreSelection() {
    editor.focus();
    var sel = window.getSelection();
    sel.removeAllRanges();
    if (savedRange && editor.contains(savedRange.commonAncestorContainer)) {
      sel.addRange(savedRange);
    } else {
      var r = document.createRange();
      r.selectNodeContents(editor);
      r.collapse(false);
      sel.addRange(r);
    }
  }

  document.addEventListener('selectionchange', function () {
    if (document.activeElement === editor) { saveSelection(); updateActive(); }
  });

  // --- Toolbar ---------------------------------------------------------------
  var stateCmds = ['bold', 'italic', 'underline', 'strikeThrough', 'insertUnorderedList', 'insertOrderedList'];

  function updateActive() {
    if (sourceMode) return;
    var block = '';
    try { block = (document.queryCommandValue('formatBlock') || '').toLowerCase(); } catch (e) { /* ignore */ }
    toolbar.querySelectorAll('button[data-cmd]').forEach(function (b) {
      var cmd = b.dataset.cmd, on = false;
      try {
        if (stateCmds.indexOf(cmd) >= 0) on = document.queryCommandState(cmd);
        else if (cmd === 'formatBlock' && b.dataset.value !== 'p') on = block === b.dataset.value;
      } catch (e) { on = false; }
      b.classList.toggle('active', !!on);
    });
  }

  toolbar.addEventListener('mousedown', function (e) {
    if (e.target.closest('button')) e.preventDefault(); // keep editor selection
  });

  toolbar.addEventListener('click', function (e) {
    var btn = e.target.closest('button');
    if (!btn) return;
    if (btn.id === 'btn-source') { toggleSource(); return; }
    if (btn.id === 'btn-image') { saveSelection(); fileInput.click(); return; }
    if (sourceMode) return;
    var cmd = btn.dataset.cmd;
    if (!cmd) return;
    restoreSelection();
    if (cmd === 'formatBlock') {
      var current = (document.queryCommandValue('formatBlock') || '').toLowerCase();
      var value = btn.dataset.value;
      if (current === value && value !== 'p') value = 'p'; // toggle off
      document.execCommand('formatBlock', false, '<' + value + '>');
    } else if (cmd === 'createLink') {
      var url = window.prompt('Link address:', 'https://');
      if (url && url !== 'https://') {
        var sel = window.getSelection();
        if (sel.isCollapsed) {
          var a = document.createElement('a');
          a.href = url; a.textContent = url;
          document.execCommand('insertHTML', false, a.outerHTML);
        } else {
          document.execCommand('createLink', false, url);
        }
      }
    } else {
      document.execCommand(cmd, false, null);
    }
    markDirty();
    updateActive();
  });

  // --- Source (HTML) mode -------------------------------------------------------
  function prettyHtml(html) {
    return html
      .replace(/\n{2,}/g, '\n')
      .replace(/(<\/(p|h[1-6]|ul|ol|li|blockquote|pre|figure|div|table|thead|tbody|tr)>)(?!\n)/gi, '$1\n')
      .replace(/(<(ul|ol|table|thead|tbody|tr)[^>]*>)(?!\n)/gi, '$1\n')
      .replace(/(<hr[^>]*>)(?!\n)/gi, '$1\n')
      .trim();
  }

  function toggleSource() {
    var btn = document.getElementById('btn-source');
    if (!sourceMode) {
      cleanEditorMarks();
      source.value = prettyHtml(editor.innerHTML);
      editor.hidden = true; source.hidden = false; source.focus();
    } else {
      editor.innerHTML = source.value;
      source.hidden = true; editor.hidden = false; editor.focus();
    }
    sourceMode = !sourceMode;
    btn.classList.toggle('active', sourceMode);
    toolbar.querySelectorAll('button').forEach(function (b) {
      if (b !== btn) b.disabled = sourceMode;
    });
  }

  // --- Images -----------------------------------------------------------------
  function isImage(file) { return file && /^image\/(png|jpeg|gif|webp)$/i.test(file.type); }

  function insertImages(files) {
    files = Array.prototype.filter.call(files, isImage);
    if (!files.length) { setStatus('Only PNG, JPEG, GIF and WebP images can be added.'); return; }
    restoreSelection();
    files.forEach(function (file) {
      var id = 'up-' + Date.now() + '-' + Math.random().toString(36).slice(2, 7);
      var blobUrl = URL.createObjectURL(file);
      document.execCommand('insertHTML', false,
        '<p><img src="' + blobUrl + '" alt="" class="uploading" data-upload-id="' + id + '"></p><p><br></p>');
      uploadImage(file, id, blobUrl);
    });
    markDirty();
  }

  function uploadImage(file, id, blobUrl) {
    pending++;
    setStatus('Uploading ' + pending + ' image(s)…', true);
    var data = new FormData();
    data.append('file', file, file.name || 'pasted.png');
    fetch(uploadUrl, {
      method: 'POST', body: data, credentials: 'same-origin',
      headers: { 'X-CSRF-Token': csrf, 'X-Requested-With': 'fetch' }
    }).then(function (resp) {
      return resp.json().catch(function () { return { error: 'Upload failed (' + resp.status + ')' }; })
        .then(function (json) { if (!resp.ok) throw new Error(json.error || ('HTTP ' + resp.status)); return json; });
    }).then(function (json) {
      var img = editor.querySelector('img[data-upload-id="' + id + '"]');
      if (img) {
        img.src = json.url;
        img.classList.remove('uploading');
        if (!img.className) img.removeAttribute('class');
        img.removeAttribute('data-upload-id');
      }
      setStatus('Image uploaded.');
    }).catch(function (err) {
      var img = editor.querySelector('img[data-upload-id="' + id + '"]');
      if (img) img.remove();
      setStatus('Image upload failed: ' + err.message, true);
    }).finally(function () {
      pending--;
      URL.revokeObjectURL(blobUrl);
      if (pending > 0) setStatus('Uploading ' + pending + ' image(s)…', true);
    });
  }

  fileInput.addEventListener('change', function () {
    if (fileInput.files.length) insertImages(fileInput.files);
    fileInput.value = '';
  });

  // Paste: screenshots / copied images become uploads; pasted HTML is tidied.
  editor.addEventListener('paste', function (e) {
    var cd = e.clipboardData;
    if (!cd) return;
    var files = [];
    Array.prototype.forEach.call(cd.items || [], function (item) {
      if (item.kind === 'file') { var f = item.getAsFile(); if (isImage(f)) files.push(f); }
    });
    if (files.length) {
      e.preventDefault();
      saveSelection();
      insertImages(files);
      return;
    }
    setTimeout(cleanPasted, 0);
    markDirty();
  });

  function cleanPasted() {
    editor.querySelectorAll('style, script, meta, link, title').forEach(function (el) { el.remove(); });
    editor.querySelectorAll('[style]').forEach(function (el) { el.removeAttribute('style'); });
    editor.querySelectorAll('font, span').forEach(function (el) {
      if (el.tagName === 'SPAN' && el.attributes.length) return;
      var parent = el.parentNode;
      while (el.firstChild) parent.insertBefore(el.firstChild, el);
      parent.removeChild(el);
    });
  }

  // Drag & drop image files into the editor.
  editor.addEventListener('dragover', function (e) {
    if (e.dataTransfer && Array.prototype.indexOf.call(e.dataTransfer.types, 'Files') >= 0) {
      e.preventDefault();
      editor.classList.add('dragover');
    }
  });
  editor.addEventListener('dragleave', function () { editor.classList.remove('dragover'); });
  editor.addEventListener('drop', function (e) {
    editor.classList.remove('dragover');
    if (!e.dataTransfer || !e.dataTransfer.files.length) return;
    e.preventDefault();
    var range = null;
    if (document.caretRangeFromPoint) range = document.caretRangeFromPoint(e.clientX, e.clientY);
    else if (document.caretPositionFromPoint) {
      var pos = document.caretPositionFromPoint(e.clientX, e.clientY);
      if (pos) { range = document.createRange(); range.setStart(pos.offsetNode, pos.offset); }
    }
    if (range && editor.contains(range.startContainer)) savedRange = range;
    insertImages(e.dataTransfer.files);
  });

  // Click an image to select it (then Delete/Backspace removes it).
  editor.addEventListener('click', function (e) {
    editor.querySelectorAll('img.selected').forEach(function (i) { i.classList.remove('selected'); });
    if (e.target.tagName === 'IMG') {
      e.target.classList.add('selected');
      var r = document.createRange();
      r.selectNode(e.target);
      var sel = window.getSelection();
      sel.removeAllRanges(); sel.addRange(r);
    }
  });

  function cleanEditorMarks() {
    editor.querySelectorAll('img.selected').forEach(function (i) {
      i.classList.remove('selected');
      if (!i.className) i.removeAttribute('class');
    });
  }

  // --- Saving -------------------------------------------------------------------
  editor.addEventListener('input', markDirty);
  source.addEventListener('input', markDirty);
  form.querySelectorAll('input, select, textarea').forEach(function (el) {
    el.addEventListener('input', markDirty);
  });

  form.addEventListener('submit', function (e) {
    if (pending > 0) {
      e.preventDefault();
      setStatus('Please wait – images are still uploading.', true);
      return;
    }
    cleanEditorMarks();
    var html = sourceMode ? source.value : editor.innerHTML;
    if (/^\s*(<p>\s*<br\s*\/?>\s*<\/p>|<br\s*\/?>)?\s*$/i.test(html)) html = '';
    input.value = html;
    dirty = false;
  });

  document.addEventListener('keydown', function (e) {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
      e.preventDefault();
      var btn = form.querySelector('button[name="action"][value="save"]');
      if (form.requestSubmit) form.requestSubmit(btn); else btn.click();
    }
  });

  window.addEventListener('beforeunload', function (e) {
    if (dirty || pending > 0) { e.preventDefault(); e.returnValue = ''; }
  });
})();

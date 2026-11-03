# Changelog

All notable changes to NXBlogger are recorded here, one entry per day (DD-MM-YYYY).

## 03-11-2026 – version 1.0.1

### WordPress import
- Removes the empty paragraphs and extra blank lines that the WordPress block editor leaves behind, so the HTML view in the editor stays tidy.
- Tested with a real WordPress 7.1 export: 12 block-editor posts and 220 images imported, no image links left pointing to the old site.

### Blog
- Block-editor galleries (`wp-block-gallery`) are shown as an image grid; a gallery with one image is shown as a normal centred image.
- Tall portrait photos are limited to 80 % of the screen height and centred instead of filling the whole page.

### Repository
- `wp_backup/` is git-ignored, so WordPress export and backup files placed there are never committed.

## 31-10-2026 – version 1.0.0

First version.

### Blog
- Public blog with a calm light blue theme, rounded buttons and a mobile-friendly layout.
- Front page with post cards (cover image, date, summary) and pagination.
- Single post page with tags, previous/next links and a click-to-zoom image lightbox.
- Archive page (posts by year and a tag list), tag pages, search and an RSS feed (`/feed.xml`).
- Old WordPress links (`/2021/05/03/slug/`, `/2021/05/slug/`, `/?p=123`) redirect to the new post URLs.

### Maintenance
- Login for one admin user (name chosen at install, default `admin`). The password is stored only as a salted scrypt hash and can be changed under Settings.
- Login throttling (5 failed attempts per 15 minutes per IP address) and CSRF protection on all forms.
- Post list with search, a status filter, and publish/unpublish, edit and delete actions.
- Rich text editor: headings, bold/italic/underline/strikethrough, lists, quotes, code blocks, links, a divider, and an HTML source view.
- Images can be added with the Image button (several at once), by pasting a screenshot from the clipboard (Ctrl+V) or by dragging and dropping files. Inline base64 images are stored as files.
- Draft or published status, editable publish date, tags, custom URL slug and optional summary. Ctrl+S saves.
- Settings for blog title, subtitle, footer text and posts per page.
- All post HTML is sanitised before saving (no scripts or inline event handlers; iframes only from YouTube and Vimeo).

### WordPress import
- Imports WordPress WXR export files (Tools → Export) from the web page or the command line (`manage.py import-wp`).
- Converts classic-editor text into paragraphs, and handles the block editor, `[caption]`, `[gallery]`, `[embed]`, `[video]`/`[audio]` and YouTube links.
- Imports categories and tags, featured images, drafts (optional) and pages (optional).
- Copies images from a `wp-content/uploads` zip or folder, or downloads them from the old site, and rewrites image links to the local copies.
- Can be re-run safely: posts imported earlier are skipped, or updated if you choose that option.

### Deployment
- `install.sh` for Ubuntu 24.04: system packages, service user, Python venv, gunicorn systemd service, nginx HTTPS on a port you choose (default 8800), and a Let's Encrypt certificate for your domain with automatic renewal. Options `--domain`, `--title` and `--admin-user` set up your own blog. A self-signed certificate is used until Let's Encrypt succeeds.
- `update.sh`: pulls from GitHub, updates packages, backs up and migrates the database, refreshes the nginx/systemd config, restarts the service and runs a health check.
- Optional daily automatic update timer, a daily database backup timer, and `manage.py backup` for full backups.
- `nxblogger-manage` helper command on the server.

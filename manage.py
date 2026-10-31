#!/usr/bin/env python3
"""NXBlogger management commands.

  python manage.py run [--port 8800] [--host 127.0.0.1]   development server
  python manage.py init-db                                  create / migrate database
  python manage.py set-password [--password PW]            set admin password
  python manage.py import-wp export.xml [--uploads FILE_OR_DIR] [--download-images]
                                        [--include-pages] [--skip-drafts] [--update-existing]
  python manage.py backup [--dest DIR] [--keep N] [--db-only]

In production run as the service user so file ownership stays right, e.g.
  sudo -u nxblogger /opt/nxblogger/venv/bin/python /opt/nxblogger/manage.py set-password
"""
import argparse
import getpass
import sqlite3
import sys
import tarfile
from datetime import datetime
from pathlib import Path

from nxblogger import create_app
from nxblogger.auth import MIN_PASSWORD_LEN, admin_exists, set_admin_password
from nxblogger.db import connect, init_db


def cmd_run(app, args):
    app.run(host=args.host, port=args.port, debug=args.debug)


def cmd_init_db(app, _args):
    init_db(app.config["DATABASE"])
    print(f"Database ready: {app.config['DATABASE']}")


def cmd_set_password(app, args):
    user = app.config["ADMIN_USER"]
    if args.if_missing and admin_exists(app.config["DATABASE"], user):
        print(f"Admin user '{user}' already has a password; leaving it unchanged.")
        return
    password = args.password
    if not password:
        while True:
            password = getpass.getpass(f"New password for '{user}' (min {MIN_PASSWORD_LEN} chars): ")
            if password != getpass.getpass("Repeat password: "):
                print("Passwords do not match, try again.")
                continue
            if len(password) < MIN_PASSWORD_LEN:
                print("Too short, try again.")
                continue
            break
    set_admin_password(app.config["DATABASE"], user, password)
    print(f"Password set for admin user '{user}'.")


def cmd_has_admin(app, _args):
    exists = admin_exists(app.config["DATABASE"], app.config["ADMIN_USER"])
    print("yes" if exists else "no")
    sys.exit(0 if exists else 1)


def cmd_import_wp(app, args):
    from nxblogger.wpimport import ImportOptions, run_import

    opts = ImportOptions(
        include_pages=args.include_pages,
        include_drafts=not args.skip_drafts,
        update_existing=args.update_existing,
        download_images=args.download_images,
    )
    with app.app_context():
        conn = connect(app.config["DATABASE"])
        try:
            report = run_import(conn, Path(args.xml), args.uploads, opts,
                                upload_dir=Path(app.config["UPLOAD_DIR"]), log=print)
        finally:
            conn.close()
    print()
    print(f"Imported: {report.imported}  Updated: {report.updated}  Skipped: {report.skipped}")
    print(f"Files copied: {report.files_copied}  Images downloaded: {report.images_downloaded}  "
          f"Download failures: {report.image_failures}")
    for msg in report.messages:
        print(" -", msg)


def cmd_backup(app, args):
    dest = Path(args.dest or app.config["BACKUP_DIR"])
    dest.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    db_copy = dest / f"nxblogger-{stamp}.db"
    src = sqlite3.connect(str(app.config["DATABASE"]))
    try:
        out = sqlite3.connect(str(db_copy))
        with out:
            src.backup(out)
        out.close()
    finally:
        src.close()
    result = db_copy
    if not args.db_only:
        archive = dest / f"nxblogger-full-{stamp}.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(db_copy, arcname="nxblogger.db")
            tar.add(app.config["UPLOAD_DIR"], arcname="uploads")
        db_copy.unlink()
        result = archive
    print(f"Backup written: {result}")
    if args.keep:
        pattern = "nxblogger-*.db" if args.db_only else "nxblogger-full-*.tar.gz"
        old = sorted(dest.glob(pattern))[:-args.keep]
        for f in old:
            f.unlink()
            print(f"Removed old backup: {f}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="NXBlogger management")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run", help="development server")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8800)
    p.add_argument("--debug", action="store_true")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("init-db", help="create or migrate the database")
    p.set_defaults(func=cmd_init_db)

    p = sub.add_parser("set-password", help="set admin password")
    p.add_argument("--password", help="password (prompted if omitted)")
    p.add_argument("--if-missing", action="store_true", help="only set when no password exists yet")
    p.set_defaults(func=cmd_set_password)

    p = sub.add_parser("has-admin", help="exit 0 if the admin password is set")
    p.set_defaults(func=cmd_has_admin)

    p = sub.add_parser("import-wp", help="import a WordPress WXR export")
    p.add_argument("xml", help="WordPress export .xml file")
    p.add_argument("--uploads", help="zip file or directory with wp-content/uploads")
    p.add_argument("--download-images", action="store_true", help="download missing images from the old site")
    p.add_argument("--include-pages", action="store_true", help="also import WordPress pages")
    p.add_argument("--skip-drafts", action="store_true", help="do not import drafts")
    p.add_argument("--update-existing", action="store_true", help="overwrite posts imported earlier")
    p.set_defaults(func=cmd_import_wp)

    p = sub.add_parser("backup", help="back up database (and uploads)")
    p.add_argument("--dest", help="backup directory")
    p.add_argument("--keep", type=int, default=0, help="keep only the newest N backups")
    p.add_argument("--db-only", action="store_true", help="database only, no uploads")
    p.set_defaults(func=cmd_backup)

    args = parser.parse_args(argv)
    app = create_app()
    args.func(app, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())

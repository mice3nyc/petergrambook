#!/usr/bin/env python3
"""Create public-safe versions of petergram.db and peterbook.db by removing sensitive tables."""

import sqlite3
import shutil
import os

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, 'shared', 'data')

def create_public_db(src_name, dst_name, drop_tables):
    src = os.path.join(DATA, src_name)
    dst = os.path.join(DATA, dst_name)

    if not os.path.exists(src):
        print(f"ERROR: {src} not found")
        return

    # Copy original
    shutil.copy2(src, dst)
    print(f"Copied {src_name} -> {dst_name}")

    # Drop private tables
    conn = sqlite3.connect(dst)
    cur = conn.cursor()

    # List existing tables
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    existing = {row[0] for row in cur.fetchall()}
    print(f"  Existing tables: {sorted(existing)}")

    for table in drop_tables:
        if table in existing:
            cur.execute(f"DROP TABLE IF EXISTS [{table}]")
            print(f"  Dropped: {table}")
        else:
            print(f"  Skipped (not found): {table}")

    # Vacuum to reclaim space
    conn.execute("VACUUM")
    conn.commit()
    conn.close()

    src_size = os.path.getsize(src)
    dst_size = os.path.getsize(dst)
    print(f"  Size: {src_size:,} -> {dst_size:,} bytes ({dst_size/src_size*100:.1f}%)")
    print()

# Petergram: keep posts, stories, metadata; drop everything else
create_public_db(
    'petergram.db',
    'petergram_public.db',
    ['likes', 'comments', 'connections']
)

# Peterbook: keep posts, photos, albums, metadata; drop everything else
create_public_db(
    'peterbook.db',
    'peterbook_public.db',
    ['messages', 'message_threads', 'friends', 'reactions', 'ad_interests', 'search_history', 'off_meta_activity']
)

print("Done! Public DBs created.")

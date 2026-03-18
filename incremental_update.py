#!/usr/bin/env python3
"""
Petergram & Peterbook — 증분 업데이트 스크립트

새 Facebook/Instagram 백업이 도착했을 때 기존 DB에 새 데이터만 추가한다.
기존 DB를 덮어쓰지 않으며, INSERT OR IGNORE로 중복을 방지한다.

사용법:
    # Facebook 증분 업데이트
    python3 incremental_update.py --platform facebook --backup /path/to/new/facebook-backup

    # Instagram 증분 업데이트
    python3 incremental_update.py --platform instagram --backup /path/to/new/instagram-backup

    # Dry-run (실제 DB 변경 없이 추가될 데이터만 미리보기)
    python3 incremental_update.py --platform facebook --backup /path/to/backup --dry-run

    # 공개 DB도 함께 재생성
    python3 incremental_update.py --platform facebook --backup /path/to/backup --rebuild-public
"""

import argparse
import sqlite3
import shutil
import os
import re
import json
import sys
from datetime import datetime
from html.parser import HTMLParser


# ============================================================
# 경로 설정
# ============================================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "shared", "data")
PETERGRAM_DB = os.path.join(DATA_DIR, "petergram.db")
PETERBOOK_DB = os.path.join(DATA_DIR, "peterbook.db")
RELATIVE_PREFIX = "../../../_페북 인스타 백업"


# ============================================================
# 헬퍼 함수 (build_databases.py에서 재활용)
# ============================================================
class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.texts = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            self.texts.append(data)

    def get_text(self):
        return ' '.join(self.texts)


def extract_text_from_html(html_str):
    p = TextExtractor()
    p.feed(html_str)
    return p.get_text()


def parse_ig_date(date_str):
    """Parse Instagram date like 'Feb 27, 2026 3:49 pm'"""
    date_str = date_str.strip()
    formats = [
        "%b %d, %Y %I:%M %p",
        "%b %d, %Y %I:%M:%S %p",
        "%b %d, %Y, %I:%M %p",
        "%b %d, %Y, %I:%M:%S %p",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.strftime("%Y-%m-%dT%H:%M:%S"), dt.year, dt.month
        except ValueError:
            continue
    try:
        dt = datetime.strptime(date_str, "%d %b %Y, %H:%M")
        return dt.strftime("%Y-%m-%dT%H:%M:%S"), dt.year, dt.month
    except ValueError:
        pass
    return date_str, None, None


def parse_fb_date(date_str):
    """Parse Facebook date like 'Jan 5, 2024 at 3:49 PM KST'"""
    date_str = date_str.strip()
    date_str = re.sub(r'\s+(KST|UTC|EST|PST|CET|CEST|JST|EDT|PDT)\s*$', '', date_str)
    formats = [
        "%b %d, %Y at %I:%M\u202f%p",
        "%b %d, %Y at %I:%M %p",
        "%b %d, %Y, %I:%M %p",
        "%b %d, %Y %I:%M %p",
        "%b %d, %Y at %I:%M:%S\u202f%p",
        "%b %d, %Y at %I:%M:%S %p",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.strftime("%Y-%m-%dT%H:%M:%S"), dt.year, dt.month
        except ValueError:
            continue
    return date_str, None, None


def read_file(path):
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


# ============================================================
# DB 현황 조회
# ============================================================
def get_db_stats(db_path):
    """DB의 각 테이블별 레코드 수와 마지막 날짜를 반환한다."""
    if not os.path.exists(db_path):
        return {}

    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    stats = {}

    # 테이블 목록
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name != 'sqlite_sequence'")
    tables = [row[0] for row in c.fetchall()]

    for table in tables:
        c.execute(f"SELECT COUNT(*) FROM [{table}]")
        count = c.fetchone()[0]

        # date 컬럼이 있으면 마지막 날짜 조회
        c.execute(f"PRAGMA table_info([{table}])")
        columns = [col[1] for col in c.fetchall()]
        last_date = None
        if 'date' in columns:
            c.execute(f"SELECT MAX(date) FROM [{table}]")
            last_date = c.fetchone()[0]
        elif 'date_added' in columns:
            c.execute(f"SELECT MAX(date_added) FROM [{table}]")
            last_date = c.fetchone()[0]

        stats[table] = {"count": count, "last_date": last_date}

    # 메타데이터
    if 'metadata' in tables:
        c.execute("SELECT key, value FROM metadata")
        stats['_metadata'] = dict(c.fetchall())

    conn.close()
    return stats


def print_stats(stats, label="DB 현황"):
    """DB 현황을 보기 좋게 출력한다."""
    print(f"\n{'=' * 50}")
    print(f"  {label}")
    print(f"{'=' * 50}")

    if '_metadata' in stats:
        meta = stats['_metadata']
        print(f"  계정: {meta.get('account', '?')}")
        print(f"  플랫폼: {meta.get('platform', '?')}")
        print(f"  마지막 동기화: {meta.get('last_sync_date', '?')}")
        print()

    for table, info in sorted(stats.items()):
        if table.startswith('_'):
            continue
        date_str = f"  (최신: {info['last_date']})" if info['last_date'] else ""
        print(f"  {table:25s} {info['count']:>8,}건{date_str}")

    print()


# ============================================================
# 백업 경로 자동 감지
# ============================================================
def detect_backup_type(backup_path):
    """백업 폴더의 플랫폼을 자동 감지한다."""
    dirname = os.path.basename(backup_path.rstrip('/'))
    if dirname.startswith('facebook-'):
        return 'facebook'
    elif dirname.startswith('instagram-'):
        return 'instagram'

    # 내부 구조로 판단
    if os.path.exists(os.path.join(backup_path, 'your_instagram_activity')):
        return 'instagram'
    if os.path.exists(os.path.join(backup_path, 'your_facebook_activity')):
        return 'facebook'

    return None


def extract_backup_date(backup_path):
    """백업 폴더명에서 날짜를 추출한다. 예: facebook-mice3nyc-2026-06-01-xxx → 2026-06-01"""
    dirname = os.path.basename(backup_path.rstrip('/'))
    m = re.search(r'(\d{4}-\d{2}-\d{2})', dirname)
    if m:
        return m.group(1)
    return None


# ============================================================
# DB 백업 생성
# ============================================================
def create_db_backup(db_path):
    """기존 DB의 .bak 복사본을 생성한다."""
    if not os.path.exists(db_path):
        return None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak_path = f"{db_path}.bak_{timestamp}"
    shutil.copy2(db_path, bak_path)
    size_mb = os.path.getsize(bak_path) / (1024 * 1024)
    print(f"  DB 백업 생성: {os.path.basename(bak_path)} ({size_mb:.1f}MB)")
    return bak_path


# ============================================================
# Instagram 증분 업데이트
# ============================================================
def update_petergram(backup_path, dry_run=False):
    """Instagram 백업에서 새 데이터를 petergram.db에 추가한다."""
    db_path = PETERGRAM_DB
    backup_dirname = os.path.basename(backup_path.rstrip('/'))
    backup_date = extract_backup_date(backup_path)

    print(f"\n{'#' * 60}")
    print(f"# Petergram (Instagram) 증분 업데이트")
    print(f"# 백업: {backup_dirname}")
    print(f"# 백업 날짜: {backup_date or '감지 불가'}")
    print(f"# 모드: {'DRY-RUN (실제 변경 없음)' if dry_run else '실행'}")
    print(f"{'#' * 60}")

    # 1. 기존 DB 현황
    before_stats = get_db_stats(db_path)
    print_stats(before_stats, "업데이트 전 현황")

    # 2. DB 백업
    if not dry_run:
        create_db_backup(db_path)

    # 3. DB 연결 (dry_run이면 :memory:에 복사)
    if dry_run:
        conn = sqlite3.connect(":memory:")
        # 기존 DB를 메모리로 복사
        src_conn = sqlite3.connect(db_path)
        src_conn.backup(conn)
        src_conn.close()
    else:
        conn = sqlite3.connect(db_path)

    c = conn.cursor()
    added = {"posts": 0, "stories": 0, "likes": 0, "comments": 0, "connections": 0}
    skipped = {"posts": 0, "stories": 0, "likes": 0, "comments": 0, "connections": 0}

    media_prefix = RELATIVE_PREFIX + "/" + backup_dirname + "/"

    # ---- Posts ----
    print("Parsing Instagram posts...")
    posts_file = os.path.join(backup_path, "your_instagram_activity/media/posts_1.html")
    if os.path.exists(posts_file):
        content = read_file(posts_file)
        post_blocks = re.split(r'<div class="pam _3-95 _2ph- _a6-g uiBoxWhite noborder">', content)
        post_idx = 0
        for block in post_blocks[1:]:
            # Caption
            caption_match = re.search(r'<h2[^>]*>(.*?)</h2>', block, re.DOTALL)
            caption = re.sub(r'<[^>]+>', '', caption_match.group(1)).strip() if caption_match else ""

            # Date
            date_match = re.search(r'<div class="_3-94 _a6-o">(.*?)</div>', block)
            iso_date, year, month = "", None, None
            if date_match:
                date_str = re.sub(r'<[^>]+>', '', date_match.group(1)).strip()
                iso_date, year, month = parse_ig_date(date_str)

            # Media paths
            media_paths = re.findall(r'href="(media/[^"]+)"', block)
            if not media_paths:
                media_paths = re.findall(r'src="(media/[^"]+)"', block)

            # Media type
            media_type = "photo"
            if media_paths:
                exts = [os.path.splitext(p)[1].lower() for p in media_paths]
                if any(e in ('.mp4', '.mov', '.avi') for e in exts):
                    media_type = "video"
                if len(media_paths) > 1:
                    media_type = "carousel"

            # Hashtags
            hashtags = re.findall(r'#(\w+)', caption)
            hashtags_json = json.dumps(hashtags, ensure_ascii=False) if hashtags else "[]"

            # Location
            location = ""
            lat_match = re.search(r'Latitude.*?<div class="_a6-q">([\d\.\-]+)</div>', block, re.DOTALL)
            lon_match = re.search(r'Longitude.*?<div class="_a6-q">([\d\.\-]+)</div>', block, re.DOTALL)
            if lat_match and lon_match:
                location = f"{lat_match.group(1)},{lon_match.group(1)}"

            # ID
            if media_paths:
                post_id = os.path.splitext(os.path.basename(media_paths[0]))[0]
                media_path_str = media_prefix + media_paths[0]
            else:
                post_id = f"post_{post_idx}"
                media_path_str = ""

            try:
                c.execute("INSERT OR IGNORE INTO posts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (post_id, iso_date, year, month, caption, media_type, media_path_str, hashtags_json, location))
                if c.rowcount > 0:
                    added["posts"] += 1
                else:
                    skipped["posts"] += 1
            except Exception as e:
                print(f"  Post insert error: {e}")

            post_idx += 1

        print(f"  Posts 파싱 완료: {post_idx}건 중 {added['posts']}건 신규, {skipped['posts']}건 중복 스킵")
    else:
        print(f"  WARNING: Posts 파일 없음: {posts_file}")

    # ---- Stories ----
    print("Parsing Instagram stories...")
    stories_file = os.path.join(backup_path, "your_instagram_activity/media/stories.html")
    if os.path.exists(stories_file):
        content = read_file(stories_file)
        story_blocks = re.split(r'<div class="pam _3-95 _2ph- _a6-g uiBoxWhite noborder">', content)
        story_idx = 0
        for block in story_blocks[1:]:
            date_match = re.search(r'<div class="_3-94 _a6-o">(.*?)</div>', block)
            iso_date, year, month = "", None, None
            if date_match:
                date_str = re.sub(r'<[^>]+>', '', date_match.group(1)).strip()
                iso_date, year, month = parse_ig_date(date_str)

            media_paths = re.findall(r'href="(media/[^"]+)"', block)
            if not media_paths:
                media_paths = re.findall(r'src="(media/[^"]+)"', block)

            media_type = "photo"
            if media_paths:
                ext = os.path.splitext(media_paths[0])[1].lower()
                if ext in ('.mp4', '.mov'):
                    media_type = "video"
                story_id = os.path.splitext(os.path.basename(media_paths[0]))[0]
                media_path_str = media_prefix + media_paths[0]
            else:
                story_id = f"story_{story_idx}"
                media_path_str = ""

            try:
                c.execute("INSERT OR IGNORE INTO stories VALUES (?, ?, ?, ?, ?, ?)",
                         (story_id, iso_date, year, month, media_type, media_path_str))
                if c.rowcount > 0:
                    added["stories"] += 1
                else:
                    skipped["stories"] += 1
            except Exception as e:
                print(f"  Story insert error: {e}")

            story_idx += 1

        print(f"  Stories 파싱 완료: {story_idx}건 중 {added['stories']}건 신규, {skipped['stories']}건 중복 스킵")
    else:
        print(f"  WARNING: Stories 파일 없음: {stories_file}")

    # ---- Likes ----
    print("Parsing Instagram likes...")
    likes_file = os.path.join(backup_path, "your_instagram_activity/likes/liked_posts.html")
    if os.path.exists(likes_file):
        content = read_file(likes_file)
        like_blocks = re.split(r'<div class="pam _3-95 _2ph- _a6-g uiBoxWhite noborder">', content)

        # 기존 likes의 (date, target_account) 조합으로 중복 체크
        c.execute("SELECT date, target_account FROM likes")
        existing_likes = set(c.fetchall())

        like_idx = 0
        for block in like_blocks[1:]:
            account_match = re.search(r'<a[^>]*>([^<]+)</a>', block)
            account = account_match.group(1).strip() if account_match else ""

            date_match = re.search(r'<div class="_3-94 _a6-o">(.*?)</div>', block)
            iso_date = ""
            if date_match:
                date_str = re.sub(r'<[^>]+>', '', date_match.group(1)).strip()
                iso_date, _, _ = parse_ig_date(date_str)

            content_match = re.search(r'<div class="_a6-p">(.*?)</div>', block, re.DOTALL)
            content_text = re.sub(r'<[^>]+>', '', content_match.group(1)).strip() if content_match else ""

            if account or iso_date:
                if (iso_date, account) not in existing_likes:
                    c.execute("INSERT INTO likes (date, target_account, target_content) VALUES (?, ?, ?)",
                             (iso_date, account, content_text[:500] if content_text else ""))
                    existing_likes.add((iso_date, account))
                    added["likes"] += 1
                else:
                    skipped["likes"] += 1

            like_idx += 1

        print(f"  Likes 파싱 완료: {like_idx}건 중 {added['likes']}건 신규, {skipped['likes']}건 중복 스킵")

    # ---- Comments ----
    print("Parsing Instagram comments...")
    comments_file = os.path.join(backup_path, "your_instagram_activity/comments/post_comments_1.html")
    if os.path.exists(comments_file):
        content = read_file(comments_file)
        comment_blocks = re.split(r'<div class="pam _3-95 _2ph- _a6-g uiBoxWhite noborder">', content)

        # 기존 comments의 (date, comment_text 앞 100자) 조합으로 중복 체크
        c.execute("SELECT date, SUBSTR(comment_text, 1, 100) FROM comments")
        existing_comments = set(c.fetchall())

        comment_idx = 0
        for block in comment_blocks[1:]:
            comment_match = re.search(r'<div class="_a6-p">(.*?)</div>', block, re.DOTALL)
            comment_text = re.sub(r'<[^>]+>', '', comment_match.group(1)).strip() if comment_match else ""

            account_match = re.search(r'<a[^>]*>([^<]+)</a>', block)
            account = account_match.group(1).strip() if account_match else ""

            date_match = re.search(r'<div class="_3-94 _a6-o">(.*?)</div>', block)
            iso_date = ""
            if date_match:
                date_str = re.sub(r'<[^>]+>', '', date_match.group(1)).strip()
                iso_date, _, _ = parse_ig_date(date_str)

            if comment_text or account:
                key = (iso_date, comment_text[:100])
                if key not in existing_comments:
                    c.execute("INSERT INTO comments (date, target_account, comment_text) VALUES (?, ?, ?)",
                             (iso_date, account, comment_text))
                    existing_comments.add(key)
                    added["comments"] += 1
                else:
                    skipped["comments"] += 1

            comment_idx += 1

        print(f"  Comments 파싱 완료: {comment_idx}건 중 {added['comments']}건 신규, {skipped['comments']}건 중복 스킵")

    # ---- Followers / Following ----
    print("Parsing Instagram connections (followers/following)...")
    for conn_type, file_subpath in [
        ("follower", "connections/followers_and_following/followers_1.html"),
        ("following", "connections/followers_and_following/following.html"),
    ]:
        conn_file = os.path.join(backup_path, file_subpath)
        if not os.path.exists(conn_file):
            print(f"  {conn_type} 파일 없음: {conn_file}")
            continue

        content = read_file(conn_file)
        blocks = re.split(r'<div class="pam _3-95 _2ph- _a6-g uiBoxWhite noborder">', content)

        # 기존 connections 중복 체크
        c.execute("SELECT account_name FROM connections WHERE type=?", (conn_type,))
        existing_conns = set(row[0] for row in c.fetchall())

        conn_idx = 0
        for block in blocks[1:]:
            name_match = re.search(r'<a[^>]*href="https://www\.instagram\.com/([^"]+)"[^>]*>', block)
            if name_match:
                account = name_match.group(1).rstrip('/')
            else:
                name_match = re.search(r'class="_a6-h[^"]*"[^>]*>(.*?)</', block, re.DOTALL)
                if name_match:
                    account = re.sub(r'<[^>]+>', '', name_match.group(1)).strip()
                else:
                    text_match = re.search(r'<div class="_a6-p">(.*?)</div>', block, re.DOTALL)
                    account = re.sub(r'<[^>]+>', '', text_match.group(1)).strip() if text_match else ""

            date_match = re.search(r'<div class="_3-94 _a6-o">(.*?)</div>', block)
            iso_date = ""
            if date_match:
                date_str = re.sub(r'<[^>]+>', '', date_match.group(1)).strip()
                iso_date, _, _ = parse_ig_date(date_str)

            if account:
                if account not in existing_conns:
                    c.execute("INSERT INTO connections (type, account_name, date) VALUES (?, ?, ?)",
                             (conn_type, account, iso_date))
                    existing_conns.add(account)
                    added["connections"] += 1
                else:
                    skipped["connections"] += 1

            conn_idx += 1

        print(f"  {conn_type} 파싱 완료: {conn_idx}건 중 새로 추가된 것 포함")

    # 4. 메타데이터 업데이트
    if backup_date and not dry_run:
        c.execute("INSERT OR REPLACE INTO metadata VALUES ('last_sync_date', ?)", (backup_date,))
        c.execute("INSERT OR REPLACE INTO metadata VALUES ('last_incremental_update', ?)",
                 (datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),))

    # 5. 커밋
    if not dry_run:
        conn.commit()

    # 6. 결과 출력
    after_stats = get_db_stats(db_path) if not dry_run else _get_memory_db_stats(conn)
    print_update_summary("Petergram", added, skipped, before_stats, after_stats)

    conn.close()
    return added


def _get_memory_db_stats(conn):
    """메모리 DB에서 stats를 추출한다 (dry-run용)."""
    c = conn.cursor()
    stats = {}
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name != 'sqlite_sequence'")
    tables = [row[0] for row in c.fetchall()]
    for table in tables:
        c.execute(f"SELECT COUNT(*) FROM [{table}]")
        count = c.fetchone()[0]
        c.execute(f"PRAGMA table_info([{table}])")
        columns = [col[1] for col in c.fetchall()]
        last_date = None
        if 'date' in columns:
            c.execute(f"SELECT MAX(date) FROM [{table}]")
            last_date = c.fetchone()[0]
        elif 'date_added' in columns:
            c.execute(f"SELECT MAX(date_added) FROM [{table}]")
            last_date = c.fetchone()[0]
        stats[table] = {"count": count, "last_date": last_date}
    return stats


# ============================================================
# Facebook 증분 업데이트
# ============================================================
def update_peterbook(backup_path, dry_run=False):
    """Facebook 백업에서 새 데이터를 peterbook.db에 추가한다."""
    db_path = PETERBOOK_DB
    backup_dirname = os.path.basename(backup_path.rstrip('/'))
    backup_date = extract_backup_date(backup_path)

    # Facebook은 여러 분할 백업이 올 수 있다.
    # 사용자가 지정한 경로가 메인 백업(포스트/친구/반응 등이 있는 것)인지,
    # 메신저 백업인지, 미디어 백업인지 자동 감지한다.
    has_posts = os.path.exists(os.path.join(backup_path, "your_facebook_activity/posts"))
    has_messages = os.path.exists(os.path.join(backup_path, "your_facebook_activity/messages"))
    has_connections = os.path.exists(os.path.join(backup_path, "connections"))

    print(f"\n{'#' * 60}")
    print(f"# Peterbook (Facebook) 증분 업데이트")
    print(f"# 백업: {backup_dirname}")
    print(f"# 백업 날짜: {backup_date or '감지 불가'}")
    print(f"# 포스트: {'있음' if has_posts else '없음'}")
    print(f"# 메시지: {'있음' if has_messages else '없음'}")
    print(f"# 연결 정보: {'있음' if has_connections else '없음'}")
    print(f"# 모드: {'DRY-RUN (실제 변경 없음)' if dry_run else '실행'}")
    print(f"{'#' * 60}")

    # 1. 기존 DB 현황
    before_stats = get_db_stats(db_path)
    print_stats(before_stats, "업데이트 전 현황")

    # 2. DB 백업
    if not dry_run:
        create_db_backup(db_path)

    # 3. DB 연결
    if dry_run:
        conn = sqlite3.connect(":memory:")
        src_conn = sqlite3.connect(db_path)
        src_conn.backup(conn)
        src_conn.close()
    else:
        conn = sqlite3.connect(db_path)

    c = conn.cursor()
    added = {"posts": 0, "messages": 0, "message_threads": 0,
             "reactions": 0, "friends": 0, "ad_interests": 0,
             "search_history": 0, "off_meta_activity": 0, "photos": 0}
    skipped = {"posts": 0, "messages": 0, "message_threads": 0,
               "reactions": 0, "friends": 0, "ad_interests": 0,
               "search_history": 0, "off_meta_activity": 0, "photos": 0}

    media_prefix = RELATIVE_PREFIX + "/" + backup_dirname + "/"

    # ---- Posts ----
    if has_posts:
        print("Parsing Facebook posts...")
        posts_file = os.path.join(backup_path, "your_facebook_activity/posts/your_posts__check_ins__photos_and_videos_1.html")
        if os.path.exists(posts_file):
            content = read_file(posts_file)
            post_blocks = re.split(r'<div class="pam _3-95 _2ph- _a6-g uiBoxWhite noborder">', content)

            # 기존 post 내용으로 중복 체크 (id는 순번 기반이라 백업마다 달라질 수 있음)
            # date + content 앞 200자 조합으로 중복 판별
            c.execute("SELECT date, SUBSTR(content, 1, 200) FROM posts")
            existing_posts = set(c.fetchall())

            # 기존 최대 post ID 번호 추출
            c.execute("SELECT id FROM posts WHERE id LIKE 'fb_post_%' ORDER BY CAST(SUBSTR(id, 9) AS INTEGER) DESC LIMIT 1")
            row = c.fetchone()
            if row:
                max_post_num = int(row[0].replace('fb_post_', '').split('_')[0]) if row else 0
                # 더 정확하게: fb_YYMMDD_NNNN 형식일 수도 있음
                c.execute("SELECT MAX(CAST(REPLACE(REPLACE(id, 'fb_post_', ''), 'fb_', '') AS INTEGER)) FROM posts")
                r = c.fetchone()
                next_post_idx = (r[0] if r and r[0] else 0) + 1
            else:
                next_post_idx = 0

            # 기존 ID 패턴 확인
            c.execute("SELECT id FROM posts LIMIT 5")
            sample_ids = [row[0] for row in c.fetchall()]
            id_has_date_format = any(re.match(r'fb_\d{6}_\d+', sid) for sid in sample_ids)

            post_idx = 0
            for block in post_blocks[1:]:
                # Content
                content_match = re.search(r'<div class="_2pin _a6-p">(.*?)</div>', block, re.DOTALL)
                if not content_match:
                    content_match = re.search(r'<div class="_a6-p">(.*?)</div>', block, re.DOTALL)
                post_content = re.sub(r'<[^>]+>', '', content_match.group(1)).strip() if content_match else ""
                if not post_content:
                    h2_match = re.search(r'<h2[^>]*>(.*?)</h2>', block, re.DOTALL)
                    if h2_match:
                        post_content = re.sub(r'<[^>]+>', '', h2_match.group(1)).strip()

                # Date
                date_match = re.search(r'<div class="_3-94 _a6-o">(.*?)</div>', block)
                iso_date, year, month = "", None, None
                if date_match:
                    date_str = re.sub(r'<[^>]+>', '', date_match.group(1)).strip()
                    iso_date, year, month = parse_fb_date(date_str)

                # Media
                media_paths = re.findall(r'href="([^"]*media[^"]*)"', block)
                if not media_paths:
                    media_paths = re.findall(r'src="([^"]*media[^"]*)"', block)
                media_type = "text"
                media_path_str = ""
                if media_paths:
                    exts = [os.path.splitext(p)[1].lower() for p in media_paths]
                    if any(e in ('.mp4', '.mov') for e in exts):
                        media_type = "video"
                    elif any(e in ('.jpg', '.jpeg', '.png', '.gif') for e in exts):
                        media_type = "photo"
                    media_path_str = media_prefix + media_paths[0]

                # 중복 체크: date + content 조합
                dup_key = (iso_date, (post_content[:200] if post_content else ""))
                if post_content or media_paths:
                    if dup_key not in existing_posts:
                        # 기존 ID 패턴에 맞춰 새 ID 생성
                        if id_has_date_format and year and month:
                            # fb_YYMMDD_NNNN 형식
                            date_part = iso_date[:10].replace('-', '')[2:] if iso_date else "000000"
                            post_id = f"fb_{date_part}_{next_post_idx}"
                        else:
                            post_id = f"fb_post_{next_post_idx}"
                        next_post_idx += 1

                        try:
                            c.execute("INSERT OR IGNORE INTO posts VALUES (?, ?, ?, ?, ?, ?, ?)",
                                     (post_id, iso_date, year, month,
                                      post_content[:5000] if post_content else "", media_type, media_path_str))
                            if c.rowcount > 0:
                                added["posts"] += 1
                                existing_posts.add(dup_key)
                            else:
                                skipped["posts"] += 1
                        except Exception as e:
                            print(f"  Post insert error: {e}")
                    else:
                        skipped["posts"] += 1

                post_idx += 1

            print(f"  Posts 파싱 완료: {post_idx}건 중 {added['posts']}건 신규, {skipped['posts']}건 중복 스킵")

    # ---- Message Threads ----
    if has_messages:
        print("Parsing Facebook message threads...")
        inbox_path = os.path.join(backup_path, "your_facebook_activity/messages/inbox")
        if os.path.exists(inbox_path):
            # 기존 thread IDs
            c.execute("SELECT id FROM message_threads")
            existing_threads = set(row[0] for row in c.fetchall())

            for thread_dir in sorted(os.listdir(inbox_path)):
                thread_path = os.path.join(inbox_path, thread_dir)
                if not os.path.isdir(thread_path):
                    continue

                if thread_dir in existing_threads:
                    skipped["message_threads"] += 1
                    # 기존 스레드라도 새 메시지가 있을 수 있으므로 메시지는 따로 처리
                else:
                    html_files = [f for f in os.listdir(thread_path) if f.endswith('.html')]
                    msg_count = len(html_files)

                    media_count = 0
                    for subdir in ['photos', 'videos', 'gifs', 'audio', 'files']:
                        media_dir = os.path.join(thread_path, subdir)
                        if os.path.exists(media_dir):
                            try:
                                media_count += len(os.listdir(media_dir))
                            except:
                                pass

                    display_name = re.sub(r'_\d+$', '', thread_dir)
                    participant_count = 1
                    others_match = re.search(r'and(\d+)others', thread_dir)
                    if others_match:
                        participant_count = int(others_match.group(1)) + 2
                    elif 'and' in display_name.lower():
                        participant_count = display_name.count('and') + 2

                    last_date = ""
                    if html_files:
                        try:
                            first_html = os.path.join(thread_path, sorted(html_files)[0])
                            with open(first_html, 'r', encoding='utf-8') as f:
                                first_chunk = f.read(5000)
                            dm = re.search(r'<div class="_3-94 _a6-o">(.*?)</div>', first_chunk)
                            if dm:
                                last_date = re.sub(r'<[^>]+>', '', dm.group(1)).strip()
                        except:
                            pass

                    try:
                        c.execute("INSERT OR IGNORE INTO message_threads VALUES (?, ?, ?, ?, ?, ?)",
                                 (thread_dir, display_name, participant_count, msg_count, media_count, last_date))
                        if c.rowcount > 0:
                            added["message_threads"] += 1
                    except Exception as e:
                        print(f"  Thread insert error: {e}")

                # ---- 개별 메시지 파싱 (기존/신규 스레드 모두) ----
                html_files = [f for f in os.listdir(thread_path) if f.endswith('.html')]
                for hf in html_files:
                    hcontent = read_file(os.path.join(thread_path, hf))

                    # 메시지 블록 파싱
                    msg_blocks = re.split(r'<div class="pam _3-95 _2ph- _a6-g uiBoxWhite noborder">', hcontent)
                    for msg_block in msg_blocks[1:]:
                        # Sender
                        sender_match = re.search(r'class="_a6-h[^"]*"[^>]*>(.*?)</', msg_block, re.DOTALL)
                        sender = re.sub(r'<[^>]+>', '', sender_match.group(1)).strip() if sender_match else ""

                        # Date
                        date_match = re.search(r'<div class="_3-94 _a6-o">(.*?)</div>', msg_block)
                        iso_date = ""
                        if date_match:
                            date_str = re.sub(r'<[^>]+>', '', date_match.group(1)).strip()
                            iso_date, _, _ = parse_fb_date(date_str)

                        # Content
                        content_divs = re.findall(r'<div class="_a6-p">(.*?)</div>', msg_block, re.DOTALL)
                        msg_content = ""
                        for cd in content_divs:
                            text = re.sub(r'<[^>]+>', '', cd).strip()
                            if text and text != sender:
                                msg_content = text
                                break

                        # Media
                        msg_media_paths = re.findall(r'src="([^"]*(?:photos|videos|gifs|audio)[^"]*)"', msg_block)
                        msg_media = media_prefix + msg_media_paths[0] if msg_media_paths else ""

                        if sender and (msg_content or msg_media):
                            # 중복 체크: thread_id + sender + date
                            c.execute("SELECT COUNT(*) FROM messages WHERE thread_id=? AND sender=? AND date=? AND SUBSTR(content,1,100)=?",
                                     (thread_dir, sender, iso_date, msg_content[:100]))
                            if c.fetchone()[0] == 0:
                                c.execute("INSERT INTO messages (thread_id, sender, content, date, media_path) VALUES (?, ?, ?, ?, ?)",
                                         (thread_dir, sender, msg_content, iso_date, msg_media))
                                added["messages"] += 1
                            else:
                                skipped["messages"] += 1

            print(f"  Message threads: {added['message_threads']}건 신규, {skipped['message_threads']}건 기존")
            print(f"  Messages: {added['messages']}건 신규, {skipped['messages']}건 중복 스킵")

    # ---- Reactions ----
    if has_posts:
        print("Parsing Facebook reactions...")
        reactions_dir = os.path.join(backup_path, "your_facebook_activity/comments_and_reactions")
        if os.path.exists(reactions_dir):
            # 기존 reactions 중복 체크
            c.execute("SELECT date, reaction_type, SUBSTR(target, 1, 100) FROM reactions")
            existing_reactions = set(c.fetchall())

            reaction_files = sorted([f for f in os.listdir(reactions_dir) if f.startswith('likes_and_reactions')])
            for rf in reaction_files:
                filepath = os.path.join(reactions_dir, rf)
                try:
                    content = read_file(filepath)
                    blocks = re.split(r'<div class="pam _3-95 _2ph- _a6-g uiBoxWhite noborder">', content)
                    for block in blocks[1:]:
                        reaction_type = "like"
                        type_match = re.search(r'class="_a6-h[^"]*"[^>]*>(.*?)</', block, re.DOTALL)
                        if type_match:
                            type_text = re.sub(r'<[^>]+>', '', type_match.group(1)).strip().lower()
                            if type_text:
                                reaction_type = type_text

                        target = ""
                        link_match = re.search(r'<a[^>]*>(.*?)</a>', block)
                        if link_match:
                            target = re.sub(r'<[^>]+>', '', link_match.group(1)).strip()

                        date_match = re.search(r'<div class="_3-94 _a6-o">(.*?)</div>', block)
                        iso_date = ""
                        if date_match:
                            date_str = re.sub(r'<[^>]+>', '', date_match.group(1)).strip()
                            iso_date, _, _ = parse_fb_date(date_str)

                        if target or iso_date:
                            dup_key = (iso_date, reaction_type, target[:100] if target else "")
                            if dup_key not in existing_reactions:
                                c.execute("INSERT INTO reactions (date, reaction_type, target) VALUES (?, ?, ?)",
                                         (iso_date, reaction_type, target[:500] if target else ""))
                                existing_reactions.add(dup_key)
                                added["reactions"] += 1
                            else:
                                skipped["reactions"] += 1
                except Exception as e:
                    print(f"  Error reading {rf}: {e}")

            print(f"  Reactions 파싱 완료: {added['reactions']}건 신규, {skipped['reactions']}건 중복 스킵")

    # ---- Friends ----
    if has_connections:
        print("Parsing Facebook friends...")
        friends_file = os.path.join(backup_path, "connections/friends/your_friends.html")
        if os.path.exists(friends_file):
            # 기존 friends 중복 체크 (이름 기반)
            c.execute("SELECT name FROM friends")
            existing_friends = set(row[0] for row in c.fetchall())

            content = read_file(friends_file)
            friend_blocks = re.split(r'<div class="pam _3-95 _2ph- _a6-g uiBoxWhite noborder">', content)

            for block in friend_blocks[1:]:
                name_match = re.search(r'class="_a6-h[^"]*"[^>]*>(.*?)</', block, re.DOTALL)
                if not name_match:
                    name_match = re.search(r'<a[^>]*>(.*?)</a>', block)
                name = re.sub(r'<[^>]+>', '', name_match.group(1)).strip() if name_match else ""

                date_match = re.search(r'<div class="_3-94 _a6-o">(.*?)</div>', block)
                iso_date = ""
                if date_match:
                    date_str = re.sub(r'<[^>]+>', '', date_match.group(1)).strip()
                    iso_date, _, _ = parse_fb_date(date_str)

                if name:
                    if name not in existing_friends:
                        c.execute("INSERT INTO friends (name, date_added) VALUES (?, ?)", (name, iso_date))
                        existing_friends.add(name)
                        added["friends"] += 1
                    else:
                        skipped["friends"] += 1

            print(f"  Friends 파싱 완료: {added['friends']}건 신규, {skipped['friends']}건 중복 스킵")

    # ---- Ad Interests ----
    if has_posts:
        print("Parsing Facebook ad interests...")
        ad_prefs_file = os.path.join(backup_path, "ads_information/ad_preferences.html")
        if os.path.exists(ad_prefs_file):
            c.execute("SELECT interest FROM ad_interests")
            existing_interests = set(row[0] for row in c.fetchall())

            content = read_file(ad_prefs_file)
            interest_blocks = re.split(r'<div class="pam _3-95 _2ph- _a6-g uiBoxWhite noborder">', content)
            current_category = "General"
            for block in interest_blocks[1:]:
                cat_match = re.search(r'<h2[^>]*>(.*?)</h2>', block, re.DOTALL)
                if cat_match:
                    current_category = re.sub(r'<[^>]+>', '', cat_match.group(1)).strip()

                text_match = re.search(r'class="_a6-h[^"]*"[^>]*>(.*?)</', block, re.DOTALL)
                if not text_match:
                    text_match = re.search(r'<div class="_a6-p">(.*?)</div>', block, re.DOTALL)
                interest = re.sub(r'<[^>]+>', '', text_match.group(1)).strip() if text_match else ""

                if interest:
                    if interest not in existing_interests:
                        c.execute("INSERT INTO ad_interests (category, interest) VALUES (?, ?)",
                                 (current_category, interest))
                        existing_interests.add(interest)
                        added["ad_interests"] += 1
                    else:
                        skipped["ad_interests"] += 1

            print(f"  Ad interests 파싱 완료: {added['ad_interests']}건 신규, {skipped['ad_interests']}건 중복 스킵")

    # ---- Search History ----
    if has_posts:
        print("Parsing Facebook search history...")
        search_file = os.path.join(backup_path, "logged_information/search/your_search_history.html")
        if os.path.exists(search_file):
            c.execute("SELECT date, query FROM search_history")
            existing_searches = set(c.fetchall())

            content = read_file(search_file)
            search_blocks = re.split(r'<div class="pam _3-95 _2ph- _a6-g uiBoxWhite noborder">', content)
            for block in search_blocks[1:]:
                query_match = re.search(r'class="_a6-h[^"]*"[^>]*>(.*?)</', block, re.DOTALL)
                if not query_match:
                    query_match = re.search(r'<div class="_2pin _a6-p">(.*?)</div>', block, re.DOTALL)
                if not query_match:
                    query_match = re.search(r'<div class="_a6-p">(.*?)</div>', block, re.DOTALL)
                query = re.sub(r'<[^>]+>', '', query_match.group(1)).strip() if query_match else ""

                date_match = re.search(r'<div class="_3-94 _a6-o">(.*?)</div>', block)
                iso_date = ""
                if date_match:
                    date_str = re.sub(r'<[^>]+>', '', date_match.group(1)).strip()
                    iso_date, _, _ = parse_fb_date(date_str)

                if query:
                    if (iso_date, query) not in existing_searches:
                        c.execute("INSERT INTO search_history (date, query) VALUES (?, ?)", (iso_date, query))
                        existing_searches.add((iso_date, query))
                        added["search_history"] += 1
                    else:
                        skipped["search_history"] += 1

            print(f"  Search history 파싱 완료: {added['search_history']}건 신규, {skipped['search_history']}건 중복 스킵")

    # ---- Off-Meta Activity ----
    if has_posts:
        print("Parsing Off-Meta activity...")
        off_meta_file = os.path.join(backup_path, "apps_and_websites_off_of_facebook/your_activity_off_meta_technologies.html")
        if os.path.exists(off_meta_file):
            c.execute("SELECT site FROM off_meta_activity")
            existing_sites = set(row[0] for row in c.fetchall())

            content = read_file(off_meta_file)
            blocks = re.split(r'<div class="pam _3-95 _2ph- _a6-g uiBoxWhite noborder">', content)
            for block in blocks[1:]:
                site_match = re.search(r'class="_a6-h[^"]*"[^>]*>(.*?)</', block, re.DOTALL)
                site = re.sub(r'<[^>]+>', '', site_match.group(1)).strip() if site_match else ""

                activity_type = ""
                event_count = 0
                text_match = re.search(r'<div class="_a6-p">(.*?)</div>', block, re.DOTALL)
                if text_match:
                    text = re.sub(r'<[^>]+>', '', text_match.group(1)).strip()
                    count_match = re.search(r'(\d+)', text)
                    if count_match:
                        event_count = int(count_match.group(1))
                    activity_type = text

                if site:
                    if site not in existing_sites:
                        c.execute("INSERT INTO off_meta_activity (site, activity_type, event_count) VALUES (?, ?, ?)",
                                 (site, activity_type, event_count))
                        existing_sites.add(site)
                        added["off_meta_activity"] += 1
                    else:
                        skipped["off_meta_activity"] += 1

            print(f"  Off-Meta 파싱 완료: {added['off_meta_activity']}건 신규, {skipped['off_meta_activity']}건 중복 스킵")

    # 4. 메타데이터 업데이트
    if backup_date and not dry_run:
        c.execute("INSERT OR REPLACE INTO metadata VALUES ('last_sync_date', ?)", (backup_date,))
        c.execute("INSERT OR REPLACE INTO metadata VALUES ('last_incremental_update', ?)",
                 (datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),))

    # 5. 커밋
    if not dry_run:
        conn.commit()

    # 6. 결과 출력
    after_stats = get_db_stats(db_path) if not dry_run else _get_memory_db_stats(conn)
    print_update_summary("Peterbook", added, skipped, before_stats, after_stats)

    conn.close()
    return added


# ============================================================
# 결과 요약 출력
# ============================================================
def print_update_summary(platform, added, skipped, before_stats, after_stats):
    """증분 업데이트 결과를 요약 출력한다."""
    total_added = sum(added.values())
    total_skipped = sum(skipped.values())

    print(f"\n{'=' * 60}")
    print(f"  {platform} 증분 업데이트 결과")
    print(f"{'=' * 60}")
    print(f"\n  {'테이블':25s} {'이전':>8s} {'이후':>8s} {'추가':>8s} {'스킵':>8s}")
    print(f"  {'-' * 53}")

    for table in sorted(added.keys()):
        before_count = before_stats.get(table, {}).get("count", 0)
        after_count = after_stats.get(table, {}).get("count", 0)
        add_count = added[table]
        skip_count = skipped[table]
        if add_count > 0 or skip_count > 0:
            print(f"  {table:25s} {before_count:>8,} {after_count:>8,} {'+' + str(add_count):>8s} {skip_count:>8,}")

    print(f"  {'-' * 53}")
    print(f"  {'합계':25s} {'':>8s} {'':>8s} {'+' + str(total_added):>8s} {total_skipped:>8,}")

    if total_added == 0:
        print(f"\n  * 새로 추가된 데이터가 없습니다. 이미 최신 상태이거나 새 백업에 새 데이터가 없습니다.")
    else:
        print(f"\n  * {total_added}건의 새 데이터가 추가되었습니다.")

    print()


# ============================================================
# 공개 DB 재생성
# ============================================================
def rebuild_public_dbs():
    """create_public_dbs.py의 로직을 재활용하여 공개 DB를 재생성한다."""
    print(f"\n{'#' * 60}")
    print(f"# 공개 DB 재생성")
    print(f"{'#' * 60}")

    def create_public_db(src_name, dst_name, drop_tables):
        src = os.path.join(DATA_DIR, src_name)
        dst = os.path.join(DATA_DIR, dst_name)

        if not os.path.exists(src):
            print(f"  ERROR: {src} not found")
            return

        shutil.copy2(src, dst)
        print(f"  Copied {src_name} -> {dst_name}")

        conn = sqlite3.connect(dst)
        cur = conn.cursor()

        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        existing = {row[0] for row in cur.fetchall()}

        for table in drop_tables:
            if table in existing:
                cur.execute(f"DROP TABLE IF EXISTS [{table}]")
                print(f"    Dropped: {table}")

        conn.execute("VACUUM")
        conn.commit()
        conn.close()

        src_size = os.path.getsize(src)
        dst_size = os.path.getsize(dst)
        print(f"    Size: {src_size:,} -> {dst_size:,} bytes ({dst_size / src_size * 100:.1f}%)")

    create_public_db(
        'petergram.db', 'petergram_public.db',
        ['likes', 'comments', 'connections']
    )
    create_public_db(
        'peterbook.db', 'peterbook_public.db',
        ['messages', 'message_threads', 'friends', 'reactions',
         'ad_interests', 'search_history', 'off_meta_activity']
    )
    print("\n  공개 DB 재생성 완료!")


# ============================================================
# 메인
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="Petergram & Peterbook 증분 업데이트 — 새 백업 데이터를 기존 DB에 추가",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  # Facebook 증분 업데이트
  python3 incremental_update.py --platform facebook \\
      --backup ~/Desktop/myLocal/_페북\\ 인스타\\ 백업/facebook-mice3nyc-2026-06-01-xxxxxxxx

  # Instagram dry-run (미리보기)
  python3 incremental_update.py --platform instagram \\
      --backup ~/Desktop/myLocal/_페북\\ 인스타\\ 백업/instagram-peter_nolgong-2026-06-01-xxxxxxxx \\
      --dry-run

  # 플랫폼 자동 감지 + 공개 DB 재생성
  python3 incremental_update.py \\
      --backup ~/Desktop/myLocal/_페북\\ 인스타\\ 백업/facebook-mice3nyc-2026-06-01-xxxxxxxx \\
      --rebuild-public
        """
    )

    parser.add_argument('--platform', choices=['facebook', 'instagram'],
                       help='플랫폼 (미지정 시 백업 경로에서 자동 감지)')
    parser.add_argument('--backup', required=True,
                       help='새 백업 폴더 경로')
    parser.add_argument('--dry-run', action='store_true',
                       help='실제 DB 변경 없이 추가될 데이터만 미리보기')
    parser.add_argument('--rebuild-public', action='store_true',
                       help='증분 업데이트 후 공개 DB도 재생성')

    args = parser.parse_args()

    # 경로 검증
    backup_path = os.path.expanduser(args.backup)
    if not os.path.exists(backup_path):
        print(f"ERROR: 백업 경로가 존재하지 않습니다: {backup_path}")
        sys.exit(1)

    if not os.path.isdir(backup_path):
        print(f"ERROR: 백업 경로가 디렉토리가 아닙니다: {backup_path}")
        sys.exit(1)

    # 플랫폼 감지
    platform = args.platform
    if not platform:
        platform = detect_backup_type(backup_path)
        if not platform:
            print("ERROR: 플랫폼을 자동 감지할 수 없습니다. --platform 옵션을 사용하세요.")
            sys.exit(1)
        print(f"플랫폼 자동 감지: {platform}")

    # DB 존재 확인
    if platform == 'instagram':
        if not os.path.exists(PETERGRAM_DB):
            print(f"ERROR: 기존 DB가 없습니다: {PETERGRAM_DB}")
            print("  먼저 build_databases.py로 초기 DB를 생성하세요.")
            sys.exit(1)
    elif platform == 'facebook':
        if not os.path.exists(PETERBOOK_DB):
            print(f"ERROR: 기존 DB가 없습니다: {PETERBOOK_DB}")
            print("  먼저 build_databases.py로 초기 DB를 생성하세요.")
            sys.exit(1)

    # 실행
    print(f"\n{'*' * 60}")
    print(f"* Petergram & Peterbook 증분 업데이트")
    print(f"* 시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'*' * 60}")

    try:
        if platform == 'instagram':
            result = update_petergram(backup_path, dry_run=args.dry_run)
        elif platform == 'facebook':
            result = update_peterbook(backup_path, dry_run=args.dry_run)

        # 공개 DB 재생성
        if args.rebuild_public and not args.dry_run:
            rebuild_public_dbs()
        elif args.rebuild_public and args.dry_run:
            print("\n  (dry-run 모드에서는 공개 DB 재생성을 건너뜁니다)")

    except Exception as e:
        print(f"\n!!! ERROR: 업데이트 중 오류 발생 !!!")
        print(f"    {type(e).__name__}: {e}")
        print(f"\n    DB .bak 파일에서 복원 가능합니다:")
        print(f"    cp {PETERGRAM_DB}.bak_* {PETERGRAM_DB}")
        print(f"    cp {PETERBOOK_DB}.bak_* {PETERBOOK_DB}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    print(f"\n{'*' * 60}")
    print(f"* 완료: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'*' * 60}")


if __name__ == "__main__":
    main()

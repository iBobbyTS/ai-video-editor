#!/usr/bin/env python3
"""
Upload photos or reels to the Facebook Page via Graph API.

Supports single-photo, multi-photo posts, and Reels on the ModernHackers FB Page.
Uses the same credentials file as upload_instagram.py.

Usage:
  python upload_facebook.py                       # all photos from config as multi-photo post
  python upload_facebook.py --photo path/img.jpg  # single photo
  python upload_facebook.py --photo               # latest photo from config
  python upload_facebook.py --all                  # all photos from config directory
  python upload_facebook.py --video path/reel.mp4 # upload as Facebook Reel
  python upload_facebook.py --caption "My post"   # custom caption

Credentials file: instagram_credentials.json
Config: project_config.json (facebook / project section)
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


# Graph API version
API_VERSION = "v21.0"
API_BASE = f"https://graph.facebook.com/{API_VERSION}"

# Retry settings
MAX_RETRIES = 3
RETRIABLE_HTTP_CODES = {500, 502, 503, 504}


def load_credentials(creds_file):
    """Load Meta API credentials."""
    creds_path = Path(creds_file)
    if not creds_path.exists():
        print(f"ERROR: Credentials file not found: {creds_file}")
        print("See INSTAGRAM_SETUP.md for setup instructions.")
        sys.exit(1)

    with open(creds_path) as f:
        creds = json.load(f)

    required = ["page_id", "page_access_token"]
    missing = [k for k in required if not creds.get(k)]
    if missing:
        print(f"ERROR: Missing fields in {creds_file}: {', '.join(missing)}")
        sys.exit(1)

    return creds


def load_config(config_path):
    """Load project configuration."""
    if Path(config_path).exists():
        with open(config_path) as f:
            return json.load(f)
    return {}


def api_request(url, data=None, method="GET", headers=None, timeout=60, raw_data=None):
    """Make a Graph API request with retry logic."""
    for attempt in range(MAX_RETRIES + 1):
        try:
            if raw_data is not None:
                body = raw_data
            elif data is not None:
                body = urllib.parse.urlencode(data).encode()
            else:
                body = None

            req = urllib.request.Request(url, data=body, method=method)
            if headers:
                for k, v in headers.items():
                    req.add_header(k, v)

            resp = urllib.request.urlopen(req, timeout=timeout)
            return json.loads(resp.read())

        except urllib.error.HTTPError as e:
            err_body = e.read().decode()
            if e.code in RETRIABLE_HTTP_CODES and attempt < MAX_RETRIES:
                wait = 2 ** (attempt + 1)
                print(f"  Retriable HTTP {e.code}, retrying in {wait}s...")
                time.sleep(wait)
                continue
            try:
                err_json = json.loads(err_body)
                msg = err_json.get("error", {}).get("error_user_msg") or err_json.get("error", {}).get("message", err_body)
            except json.JSONDecodeError:
                msg = err_body
            raise RuntimeError(f"HTTP {e.code}: {msg}")

        except urllib.error.URLError as e:
            if attempt < MAX_RETRIES:
                wait = 2 ** (attempt + 1)
                print(f"  Network error: {e.reason}, retrying in {wait}s...")
                time.sleep(wait)
                continue
            raise RuntimeError(f"Network error: {e.reason}")

    raise RuntimeError("Max retries exceeded")


def upload_photo(page_id, token, photo_path, published=False, message=None):
    """
    Upload a photo to the Facebook Page.
    Returns the Facebook Photo ID.
    """
    boundary = f"----PythonBoundary{os.getpid()}"
    with open(photo_path, "rb") as f:
        photo_data = f.read()

    fields = [
        ("published", str(published).lower()),
        ("access_token", token),
    ]
    if message and published:
        fields.append(("message", message))

    body = b""
    for key, val in fields:
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{val}\r\n".encode()

    filename = Path(photo_path).name
    body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"source\"; filename=\"{filename}\"\r\nContent-Type: image/jpeg\r\n\r\n".encode()
    body += photo_data
    body += f"\r\n--{boundary}--\r\n".encode()

    result = api_request(
        f"{API_BASE}/{page_id}/photos",
        raw_data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        timeout=120,
    )
    return result["id"]


def publish_multi_photo_post(page_id, token, fb_photo_ids, message):
    """
    Publish a Facebook Page post with multiple photos.
    Uses /{page_id}/feed with attached_media referencing
    previously uploaded (unpublished) photo IDs.
    Returns the post ID.
    """
    data = {
        "message": message,
        "access_token": token,
    }
    for i, photo_id in enumerate(fb_photo_ids):
        data[f"attached_media[{i}]"] = json.dumps({"media_fbid": photo_id})

    result = api_request(f"{API_BASE}/{page_id}/feed", data=data, method="POST", timeout=30)
    return result["id"]


def upload_video_reel(page_id, token, video_path, description=""):
    """
    Upload a video as a Reel to the Facebook Page.
    Uses /{page_id}/videos with published=true.
    Returns (video_id, permalink).
    """
    boundary = f"----PythonBoundary{os.getpid()}"
    with open(video_path, "rb") as f:
        video_data = f.read()

    fields = [
        ("published", "true"),
        ("access_token", token),
    ]
    if description:
        fields.append(("description", description))

    body = b""
    for key, val in fields:
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{val}\r\n".encode()

    filename = Path(video_path).name
    body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"source\"; filename=\"{filename}\"\r\nContent-Type: video/mp4\r\n\r\n".encode()
    body += video_data
    body += f"\r\n--{boundary}--\r\n".encode()

    result = api_request(
        f"{API_BASE}/{page_id}/videos",
        raw_data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        timeout=300,
    )
    video_id = result["id"]

    # Try to get permalink
    try:
        url = f"{API_BASE}/{video_id}?fields=permalink_url&access_token={urllib.parse.quote(token)}"
        info = api_request(url)
        permalink = info.get("permalink_url", "")
    except Exception:
        permalink = ""

    return video_id, permalink


def find_photos_from_config(config):
    """Find photo files from the config paths.photos directory."""
    photos_dir = config.get("paths", {}).get("photos", "")
    if not photos_dir:
        return []
    photos_path = Path(photos_dir)
    if not photos_path.is_dir():
        return []
    exts = {".jpg", ".jpeg", ".png"}
    return sorted(
        [p for p in photos_path.iterdir() if p.is_file() and p.suffix.lower() in exts],
        key=lambda p: p.name,
    )


def build_caption(config, args_caption=None):
    """Build caption from config and/or CLI args.
    Reads from project section first, facebook section can override."""
    project_cfg = config.get("project", {})
    fb_cfg = config.get("facebook", {})

    if args_caption:
        caption = args_caption
    else:
        caption = fb_cfg.get("default_caption") or project_cfg.get("title", "")

    hashtags = fb_cfg.get("hashtags") or project_cfg.get("hashtags", [])
    if hashtags:
        existing_tags = {t.strip().lower() for t in caption.split() if t.startswith("#")}
        new_tags = [f"#{t}" if not t.startswith("#") else t for t in hashtags]
        new_tags = [t for t in new_tags if t.lower() not in existing_tags]
        if new_tags:
            caption = caption.rstrip() + "\n\n" + " ".join(new_tags)

    return caption.strip()


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Upload photos or reels to Facebook Page via Graph API"
    )
    parser.add_argument(
        "--photo",
        type=str,
        nargs="?",
        const="__FROM_CONFIG__",
        help="Photo file to upload (JPG/PNG). If no path given, picks latest from config",
    )
    parser.add_argument(
        "--video",
        type=str,
        help="Video file to upload as Facebook Reel (MP4)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Upload all photos from config paths.photos directory",
    )
    parser.add_argument(
        "--caption",
        type=str,
        help="Post caption (default: from config)",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="project_config.json",
        help="Project config file (default: project_config.json)",
    )
    parser.add_argument(
        "--credentials",
        type=str,
        default="instagram_credentials.json",
        help="Credentials file (default: instagram_credentials.json)",
    )

    args = parser.parse_args()

    config = load_config(args.config)
    creds = load_credentials(args.credentials)

    page_id = creds["page_id"]
    token = creds["page_access_token"]
    page_name = creds.get("page_name", page_id)

    # --- Video / Reel mode ---
    if args.video:
        video_path = Path(args.video)
        if not video_path.exists():
            print(f"ERROR: Video file not found: {video_path}")
            sys.exit(1)

        caption = build_caption(config, args.caption)
        size_mb = video_path.stat().st_size / (1024 * 1024)

        print("=" * 72)
        print("📘 Facebook Page Reel Upload")
        print("=" * 72)
        print(f"Page:        {page_name}")
        print(f"Video:       {video_path.name}")
        print(f"Size:        {size_mb:.1f} MB")
        if caption:
            display = caption[:80] + ("..." if len(caption) > 80 else "")
            print(f"Caption:     {display}")
        print("=" * 72 + "\n")

        try:
            print(f"  Uploading {video_path.name} ({size_mb:.1f} MB)...")
            video_id, permalink = upload_video_reel(page_id, token, str(video_path), caption)

            print(f"  ✓ Video ID: {video_id}")

            print("\n" + "=" * 72)
            print("✅ Facebook Reel Published")
            print("=" * 72)
            print(f"Video ID:    {video_id}")
            if permalink:
                print(f"URL:         https://www.facebook.com{permalink}")
            else:
                print(f"URL:         https://www.facebook.com/{page_id}/videos/{video_id}")
            print("=" * 72)

        except RuntimeError as e:
            print(f"\n❌ Upload failed: {e}")
            sys.exit(1)
        except Exception as e:
            print(f"\n❌ Unexpected error: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)

        return

    # --- Photo mode ---
    # Determine photos to upload
    photo_paths = []

    if args.photo and args.photo != "__FROM_CONFIG__":
        p = Path(args.photo)
        if not p.exists():
            print(f"ERROR: File not found: {p}")
            sys.exit(1)
        photo_paths = [p]

    else:
        available = find_photos_from_config(config)
        if not available:
            photos_dir = config.get("paths", {}).get("photos", "(not set)")
            print(f"ERROR: No photos found in: {photos_dir}")
            print("Set paths.photos in project_config.json or use --photo <file>")
            sys.exit(1)

        if args.all or args.photo is None:
            photo_paths = available
        else:
            # --photo with no arg: pick latest
            photo_paths = [available[-1]]

        print(f"Found {len(available)} photos in: {config['paths']['photos']}")
        for p in available:
            size = p.stat().st_size / (1024 * 1024)
            marker = " ←" if p in photo_paths else ""
            print(f"  {p.name}  ({size:.1f} MB){marker}")
        print()

    caption = build_caption(config, args.caption)

    total_mb = sum(p.stat().st_size for p in photo_paths) / (1024 * 1024)
    print("=" * 72)
    print("📘 Facebook Page Upload")
    print("=" * 72)
    print(f"Page:        {page_name}")
    print(f"Photos:      {len(photo_paths)}")
    print(f"Total size:  {total_mb:.1f} MB")
    if caption:
        display = caption[:80] + ("..." if len(caption) > 80 else "")
        print(f"Caption:     {display}")
    print("=" * 72 + "\n")

    try:
        if len(photo_paths) == 1:
            # Single photo: publish directly
            photo_path = photo_paths[0]
            name = photo_path.name
            print(f"  Uploading {name}...")
            photo_id = upload_photo(page_id, token, str(photo_path), published=True, message=caption)
            post_url = f"https://www.facebook.com/photo/?fbid={photo_id}"
            print(f"  ✓ Photo ID: {photo_id}")

            print("\n" + "=" * 72)
            print("✅ Facebook Photo Published")
            print("=" * 72)
            print(f"Photo ID:    {photo_id}")
            print(f"URL:         {post_url}")
            print("=" * 72)

        else:
            # Multi-photo: upload all as unpublished, then create feed post
            fb_photo_ids = []
            for i, photo_path in enumerate(photo_paths, 1):
                name = photo_path.name
                size_mb = photo_path.stat().st_size / (1024 * 1024)
                print(f"  [{i}/{len(photo_paths)}] {name} ({size_mb:.1f} MB)")
                fb_id = upload_photo(page_id, token, str(photo_path), published=False)
                fb_photo_ids.append(fb_id)
                print(f"    ✓ Photo ID: {fb_id}")

            print(f"\n  Publishing multi-photo post ({len(fb_photo_ids)} photos)...")
            post_id = publish_multi_photo_post(page_id, token, fb_photo_ids, caption)
            post_url = f"https://www.facebook.com/{post_id}"
            print(f"  ✓ Post ID: {post_id}")

            print("\n" + "=" * 72)
            print("✅ Facebook Multi-Photo Post Published")
            print("=" * 72)
            print(f"Post ID:     {post_id}")
            print(f"Photos:      {len(fb_photo_ids)}")
            print(f"URL:         {post_url}")
            print("=" * 72)

    except RuntimeError as e:
        print(f"\n❌ Upload failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

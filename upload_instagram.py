#!/usr/bin/env python3
"""
Upload photo or reel to Instagram via Facebook Graph API.
Uses Facebook Page as CDN relay for local file uploads.

SETUP:
======
See INSTAGRAM_SETUP.md for detailed instructions on setting up:
  - Meta Developer App
  - Facebook Page linked to Instagram Business Account
  - Permanent Page Access Token with required permissions

Required permissions:
  - instagram_basic
  - instagram_content_publish
  - pages_manage_posts (for uploading photos via Facebook CDN)
  - pages_read_engagement
  - pages_show_list

Credentials file: instagram_credentials.json
  {
    "app_id": "...",
    "ig_user_id": "...",
    "page_id": "...",
    "page_name": "...",
    "page_access_token": "..."
  }

Usage:
  python upload_instagram.py --photo assets/photos/IMG_4973.JPG --caption "My photo"
  python upload_instagram.py --video /path/to/reel.mp4 --caption "My reel"
  python upload_instagram.py --photo assets/photos/IMG_4973.JPG  (caption from config)

All settings (caption, hashtags) can be configured in project_config.json
under the "instagram" section.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
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
    """Load Instagram/Meta API credentials."""
    creds_path = Path(creds_file)
    if not creds_path.exists():
        print(f"ERROR: Credentials file not found: {creds_file}")
        print("\n" + "=" * 72)
        print("📋 INSTAGRAM SETUP REQUIRED")
        print("=" * 72)
        print("\nFollow the instructions in INSTAGRAM_SETUP.md to:")
        print("  1. Create a Meta Developer App")
        print("  2. Create/link a Facebook Page to your IG Business Account")
        print("  3. Generate a permanent Page Access Token")
        print(f"  4. Save credentials to: {creds_path.absolute()}")
        print("=" * 72)
        sys.exit(1)

    with open(creds_path) as f:
        creds = json.load(f)

    required = ["app_id", "ig_user_id", "page_id", "page_access_token"]
    missing = [k for k in required if not creds.get(k)]
    if missing:
        print(f"ERROR: Missing fields in {creds_file}: {', '.join(missing)}")
        print("See INSTAGRAM_SETUP.md for the required format.")
        sys.exit(1)

    return creds


def load_config(config_path):
    """Load project configuration."""
    if Path(config_path).exists():
        with open(config_path) as f:
            return json.load(f)
    return {}


def api_request(url, data=None, method="GET", headers=None, timeout=60, raw_data=None):
    """
    Make a Graph API request with retry logic.

    Args:
        url: Full URL
        data: Dict to form-encode as POST body
        method: HTTP method
        headers: Extra headers dict
        timeout: Request timeout in seconds
        raw_data: Raw bytes for POST body (used for multipart uploads)

    Returns:
        Parsed JSON response

    Raises:
        RuntimeError on non-retriable failure
    """
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


def upload_photo_to_facebook(page_id, token, photo_path, published=False):
    """
    Upload a photo to the Facebook Page (unpublished by default).
    Returns the Facebook Photo ID.
    """
    boundary = f"----PythonBoundary{os.getpid()}"
    with open(photo_path, "rb") as f:
        photo_data = f.read()

    # Build multipart/form-data body
    body = b""
    fields = [("published", str(published).lower()), ("access_token", token)]
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


def get_photo_cdn_url(photo_id, token):
    """Get the CDN URL of a Facebook photo."""
    url = f"{API_BASE}/{photo_id}?fields=images&access_token={urllib.parse.quote(token)}"
    result = api_request(url)
    images = result.get("images", [])
    if not images:
        raise RuntimeError(f"No image data returned for photo {photo_id}")
    # First entry is the largest resolution
    return images[0]["source"]


def create_ig_photo_container(ig_user_id, token, image_url, caption):
    """Create an Instagram media container for a single photo post."""
    data = {
        "image_url": image_url,
        "caption": caption,
        "access_token": token,
    }
    result = api_request(f"{API_BASE}/{ig_user_id}/media", data=data, method="POST", timeout=30)
    return result["id"]


def create_ig_carousel_item(ig_user_id, token, image_url):
    """Create a child container for a carousel item (no caption — caption goes on parent)."""
    data = {
        "image_url": image_url,
        "is_carousel_item": "true",
        "access_token": token,
    }
    result = api_request(f"{API_BASE}/{ig_user_id}/media", data=data, method="POST", timeout=30)
    return result["id"]


def create_ig_carousel_container(ig_user_id, token, children_ids, caption):
    """Create a carousel container referencing child items."""
    data = {
        "media_type": "CAROUSEL",
        "children": ",".join(children_ids),
        "caption": caption,
        "access_token": token,
    }
    result = api_request(f"{API_BASE}/{ig_user_id}/media", data=data, method="POST", timeout=30)
    return result["id"]


def create_ig_reel_container(ig_user_id, token, video_url, caption):
    """Create an Instagram media container for a reel."""
    data = {
        "media_type": "REELS",
        "video_url": video_url,
        "caption": caption,
        "access_token": token,
    }
    result = api_request(f"{API_BASE}/{ig_user_id}/media", data=data, method="POST", timeout=60)
    return result["id"]


def upload_video_to_facebook(page_id, token, video_path):
    """
    Upload a video to the Facebook Page (unpublished) to get a CDN URL.
    Returns a tuple of (video_id, video_url).
    """
    boundary = f"----PythonBoundary{os.getpid()}"
    with open(video_path, "rb") as f:
        video_data = f.read()

    body = b""
    fields = [("published", "false"), ("access_token", token)]
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

    # Wait for Facebook to fully process the video before getting source URL
    for attempt in range(30):
        url = f"{API_BASE}/{video_id}?fields=status,source&access_token={urllib.parse.quote(token)}"
        info = api_request(url)

        # Check processing status
        status = info.get("status", {})
        video_status = status.get("video_status", "") if isinstance(status, dict) else ""

        source = info.get("source", "")
        if source and video_status == "ready":
            return video_id, source
        if source and not video_status:
            # status field may not be present for all videos
            return video_id, source

        if attempt < 29:
            wait = 5
            progress = status.get("processing_progress", "?") if isinstance(status, dict) else "?"
            print(f"  Waiting for video processing... ({(attempt + 1) * wait}s, status={video_status or 'pending'}, progress={progress}%)")
            time.sleep(wait)

    return video_id, ""


def wait_for_container(ig_user_id, token, container_id, timeout_seconds=120):
    """
    Poll the container status until it's ready (FINISHED) or fails.
    Required for reels; photos are usually instant.
    """
    start = time.time()
    while time.time() - start < timeout_seconds:
        url = f"{API_BASE}/{container_id}?fields=status_code,status&access_token={urllib.parse.quote(token)}"
        result = api_request(url)
        status = result.get("status_code", "UNKNOWN")

        if status == "FINISHED":
            return True
        elif status == "ERROR":
            error_msg = result.get("status", "Unknown error")
            raise RuntimeError(f"Container processing failed: {error_msg}")
        elif status == "IN_PROGRESS":
            print(f"  Processing... ({int(time.time() - start)}s)", end="\r")
            time.sleep(3)
        else:
            # For photos, status_code may not be present — assume ready
            return True

    raise RuntimeError(f"Container processing timed out after {timeout_seconds}s")


def publish_container(ig_user_id, token, container_id):
    """Publish a media container to Instagram. Returns the media ID."""
    data = {
        "creation_id": container_id,
        "access_token": token,
    }
    result = api_request(f"{API_BASE}/{ig_user_id}/media_publish", data=data, method="POST", timeout=30)
    return result["id"]


def get_permalink(media_id, token):
    """Get the Instagram permalink for a published post."""
    url = f"{API_BASE}/{media_id}?fields=permalink&access_token={urllib.parse.quote(token)}"
    result = api_request(url)
    return result.get("permalink", "")


def upload_photo(creds, photo_path, caption):
    """
    Full photo upload flow:
    1. Upload to Facebook Page (unpublished) as CDN relay
    2. Get CDN URL
    3. Create IG media container
    4. Publish to Instagram
    """
    token = creds["page_access_token"]
    ig_id = creds["ig_user_id"]
    page_id = creds["page_id"]

    # Step 1: Upload to Facebook
    print("  Uploading to Facebook CDN...")
    fb_photo_id = upload_photo_to_facebook(page_id, token, photo_path)
    print(f"  ✓ Facebook Photo ID: {fb_photo_id}")

    # Step 2: Get CDN URL
    print("  Getting CDN URL...")
    cdn_url = get_photo_cdn_url(fb_photo_id, token)
    print(f"  ✓ CDN URL obtained")

    # Step 3: Create IG container
    print("  Creating Instagram container...")
    container_id = create_ig_photo_container(ig_id, token, cdn_url, caption)
    print(f"  ✓ Container ID: {container_id}")

    # Step 4: Publish
    print("  Publishing to Instagram...")
    media_id = publish_container(ig_id, token, container_id)
    permalink = get_permalink(media_id, token)

    return media_id, permalink


def upload_carousel(creds, photo_paths, caption):
    """
    Upload multiple photos as a single Instagram carousel post.
    Also uploads to Facebook CDN (unpublished) and returns FB photo IDs
    for optional Facebook Page post.
    1. Upload each photo to Facebook CDN
    2. Create carousel child containers for each
    3. Create carousel parent container
    4. Publish
    Returns (media_id, permalink, fb_photo_ids)
    """
    token = creds["page_access_token"]
    ig_id = creds["ig_user_id"]
    page_id = creds["page_id"]

    children_ids = []
    fb_photo_ids = []
    for i, photo_path in enumerate(photo_paths, 1):
        name = Path(photo_path).name
        size_mb = Path(photo_path).stat().st_size / (1024 * 1024)
        print(f"  [{i}/{len(photo_paths)}] {name} ({size_mb:.1f} MB)")

        # Upload to Facebook CDN
        print(f"    Uploading to Facebook CDN...")
        fb_photo_id = upload_photo_to_facebook(page_id, token, photo_path)
        fb_photo_ids.append(fb_photo_id)
        print(f"    ✓ Facebook Photo ID: {fb_photo_id}")

        cdn_url = get_photo_cdn_url(fb_photo_id, token)
        print(f"    ✓ CDN URL obtained")

        # Create carousel child container
        child_id = create_ig_carousel_item(ig_id, token, cdn_url)
        children_ids.append(child_id)
        print(f"    ✓ Child container: {child_id}")

    # Create carousel parent
    print(f"  Creating carousel container ({len(children_ids)} photos)...")
    container_id = create_ig_carousel_container(ig_id, token, children_ids, caption)
    print(f"  ✓ Carousel container: {container_id}")

    # Publish
    print("  Publishing to Instagram...")
    media_id = publish_container(ig_id, token, container_id)
    permalink = get_permalink(media_id, token)

    return media_id, permalink


def _ensure_h264(video_path):
    """
    Check if video is H.264. If not (e.g. HEVC), transcode to H.264.
    Returns (path_to_use, temp_file_or_None).
    The caller must delete temp_file when done.
    """
    if not shutil.which("ffprobe") or not shutil.which("ffmpeg"):
        return video_path, None

    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name", "-of", "csv=p=0", video_path],
            capture_output=True, text=True, timeout=10,
        )
        codec = probe.stdout.strip().lower()
    except Exception:
        return video_path, None

    if codec in ("h264", "avc"):
        return video_path, None

    print(f"  Video codec is {codec}, transcoding to H.264 for Instagram...")
    tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    tmp.close()
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", video_path,
             "-c:v", "libx264", "-preset", "fast", "-crf", "18",
             "-c:a", "aac", "-b:a", "192k",
             "-map", "0:v:0", "-map", "0:a:0?",
             "-movflags", "+faststart", tmp.name],
            check=True, capture_output=True, timeout=300,
        )
        size_mb = Path(tmp.name).stat().st_size / (1024 * 1024)
        print(f"  ✓ Transcoded to H.264 ({size_mb:.1f} MB)")
        return tmp.name, tmp.name
    except Exception as e:
        os.unlink(tmp.name)
        print(f"  ⚠ Transcode failed ({e}), using original")
        return video_path, None


def upload_reel(creds, video_path, caption):
    """
    Full reel upload flow:
    1. Transcode to H.264 if needed (Instagram requires it)
    2. Upload video to Facebook Page (unpublished) as CDN relay
    3. Get CDN URL
    4. Create IG reel container
    5. Wait for processing
    6. Publish to Instagram
    """
    token = creds["page_access_token"]
    ig_id = creds["ig_user_id"]
    page_id = creds["page_id"]

    # Instagram requires H.264 — transcode HEVC/other codecs
    actual_path, temp_file = _ensure_h264(video_path)

    try:
        return _do_upload_reel(page_id, ig_id, token, actual_path, caption)
    finally:
        if temp_file:
            os.unlink(temp_file)


def _do_upload_reel(page_id, ig_id, token, video_path, caption):
    """Inner reel upload using Instagram's Resumable Upload protocol."""
    size_mb = Path(video_path).stat().st_size / (1024 * 1024)
    file_size = Path(video_path).stat().st_size

    # Step 1: Create a resumable upload container
    print("  Creating Instagram reel container (resumable)...")
    data = {
        "media_type": "REELS",
        "upload_type": "resumable",
        "caption": caption,
        "access_token": token,
    }
    result = api_request(f"{API_BASE}/{ig_id}/media", data=data, method="POST", timeout=60)
    container_id = result["id"]
    upload_uri = result.get("uri", "")
    print(f"  ✓ Container ID: {container_id}")

    if not upload_uri:
        raise RuntimeError("No upload URI returned — resumable upload may not be supported for this account")

    # Step 2: Upload the video file directly to Instagram
    print(f"  Uploading video directly to Instagram ({size_mb:.1f} MB)...")
    with open(video_path, "rb") as f:
        video_data = f.read()

    upload_req = urllib.request.Request(upload_uri, data=video_data, method="POST")
    upload_req.add_header("Authorization", f"OAuth {token}")
    upload_req.add_header("offset", "0")
    upload_req.add_header("file_size", str(file_size))
    upload_req.add_header("Content-Type", "application/octet-stream")

    resp = urllib.request.urlopen(upload_req, timeout=300)
    upload_result = json.loads(resp.read())
    print(f"  ✓ Upload complete (h={upload_result.get('h', '?')})")

    # Step 3: Wait for processing
    print("  Waiting for processing...")
    wait_for_container(ig_id, token, container_id, timeout_seconds=300)
    print("  ✓ Processing complete")

    print("  Publishing to Instagram...")
    media_id = publish_container(ig_id, token, container_id)
    permalink = get_permalink(media_id, token)

    return media_id, permalink


def find_photos_from_config(config):
    """Find photo files from the config paths.photos directory."""
    photos_dir = config.get("paths", {}).get("photos", "")
    if not photos_dir:
        return []
    photos_path = Path(photos_dir)
    if not photos_path.is_dir():
        return []
    exts = {".jpg", ".jpeg", ".png"}
    photos = sorted(
        [p for p in photos_path.iterdir() if p.is_file() and p.suffix.lower() in exts],
        key=lambda p: p.name,
    )
    return photos


def build_caption(config, args_caption=None):
    """Build caption from config and/or CLI args.
    Reads from project section first, instagram section can override."""
    project_cfg = config.get("project", {})
    ig_cfg = config.get("instagram", {})

    if args_caption:
        caption = args_caption
    else:
        caption = ig_cfg.get("default_caption") or project_cfg.get("title", "")

    # Append hashtags from config if not already in caption
    hashtags = ig_cfg.get("hashtags") or project_cfg.get("hashtags", [])
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
        description="Upload photo or reel to Instagram via Facebook Graph API"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--photo",
        type=str,
        nargs="?",
        const="__FROM_CONFIG__",
        help="Photo file to upload (JPG/PNG). If no path given, picks the latest from config paths.photos"
    )
    group.add_argument(
        "--video",
        type=str,
        help="Path to video file to upload as reel (MP4)"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Upload all photos from config paths.photos directory"
    )
    parser.add_argument(
        "--caption",
        type=str,
        help="Post caption (default: from config)"
    )
    parser.add_argument(
        "--config",
        type=str,
        default="project_config.json",
        help="Project configuration file (default: project_config.json)"
    )
    parser.add_argument(
        "--credentials",
        type=str,
        default="instagram_credentials.json",
        help="Instagram credentials file (default: instagram_credentials.json)"
    )

    args = parser.parse_args()

    # Load config and credentials
    config = load_config(args.config)
    creds = load_credentials(args.credentials)

    # Build the list of files to upload
    uploads = []  # list of (media_path, media_type)

    if args.video:
        video_path = Path(args.video)
        if not video_path.exists():
            print(f"ERROR: File not found: {video_path}")
            sys.exit(1)
        uploads.append((video_path, "reel"))

    elif args.photo and args.photo != "__FROM_CONFIG__":
        # Explicit photo path
        photo_path = Path(args.photo)
        if not photo_path.exists():
            print(f"ERROR: File not found: {photo_path}")
            sys.exit(1)
        uploads.append((photo_path, "photo"))

    else:
        # Auto-discover from config paths.photos
        available = find_photos_from_config(config)
        if not available:
            photos_dir = config.get("paths", {}).get("photos", "(not set)")
            print(f"ERROR: No photos found in: {photos_dir}")
            print("Set paths.photos in project_config.json or use --photo <file>")
            sys.exit(1)

        if args.all or len(available) > 1:
            # Multiple photos → carousel post
            uploads = [(p, "carousel") for p in available]
        else:
            uploads.append((available[0], "photo"))

        print(f"Found {len(available)} photos in: {config['paths']['photos']}")
        for p in available:
            size = p.stat().st_size / (1024 * 1024)
            print(f"  {p.name}  ({size:.1f} MB)")
        print()

    # Build caption
    caption = build_caption(config, args.caption)

    # Check if this is a carousel upload (multiple photos → single post)
    carousel_photos = [p for p, t in uploads if t == "carousel"]
    if carousel_photos:
        total_mb = sum(p.stat().st_size for p in carousel_photos) / (1024 * 1024)
        print("\n" + "=" * 72)
        print("📸 Instagram Carousel Upload")
        print("=" * 72)
        print(f"Photos:      {len(carousel_photos)}")
        print(f"Total size:  {total_mb:.1f} MB")
        print(f"Account:     @{creds.get('page_name', 'unknown')}")
        if caption:
            display_caption = caption[:80] + ("..." if len(caption) > 80 else "")
            print(f"Caption:     {display_caption}")
        print("=" * 72 + "\n")

        try:
            media_id, permalink = upload_carousel(
                creds,
                [str(p) for p in carousel_photos],
                caption,
            )
            print("\n" + "=" * 72)
            print("✅ Instagram Carousel Published")
            print("=" * 72)
            print(f"Media ID:    {media_id}")
            print(f"Photos:      {len(carousel_photos)}")
            print(f"URL:         {permalink}")
            print("=" * 72)

        except RuntimeError as e:
            print(f"\n❌ Upload failed: {e}")
            sys.exit(1)
        except Exception as e:
            print(f"\n❌ Unexpected error: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)
    else:
        # Single photo or reel upload
        for media_path, media_type in uploads:
            size_mb = media_path.stat().st_size / (1024 * 1024)

            print("\n" + "=" * 72)
            print("📸 Instagram Upload")
            print("=" * 72)
            print(f"Type:        {media_type}")
            print(f"File:        {media_path}")
            print(f"Size:        {size_mb:.1f} MB")
            print(f"Account:     @{creds.get('page_name', 'unknown')}")
            if caption:
                display_caption = caption[:80] + ("..." if len(caption) > 80 else "")
                print(f"Caption:     {display_caption}")
            print("=" * 72 + "\n")

            try:
                if media_type == "photo":
                    media_id, permalink = upload_photo(creds, str(media_path), caption)
                else:
                    media_id, permalink = upload_reel(creds, str(media_path), caption)

                print("\n" + "=" * 72)
                print("✅ Upload Complete")
                print("=" * 72)
                print(f"Media ID:    {media_id}")
                print(f"URL:         {permalink}")
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

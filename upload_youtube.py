#!/usr/bin/env python3
"""
Upload video to YouTube channel with metadata.
Uses YouTube Data API v3 with OAuth 2.0 authentication.

SETUP INSTRUCTIONS:
===================

1. Install Required Packages:
   pip install google-api-python-client google-auth-oauthlib google-auth-httplib2

2. Get OAuth Credentials from Google Cloud Console:
   
   a) Go to: https://console.cloud.google.com/
   
   b) Create or Select Project:
      - Click "Select a project" → "New Project"
      - Name it (e.g., "YouTube Uploader")
      - Click "Create"
   
   c) Enable YouTube Data API v3:
      - Go to "APIs & Services" → "Library"
      - Search for "YouTube Data API v3"
      - Click on it and press "Enable"
   
   d) Create OAuth 2.0 Credentials:
      - Go to "APIs & Services" → "Credentials"
      - Click "+ CREATE CREDENTIALS" at top
      - You'll see "Help me choose" credential wizard:
        * Select an API: Choose "YouTube Data API v3"
        * What data will you be accessing?: Select "User data"
        * Click "Next"
      
      - Configure OAuth consent screen (if not already done):
        * App name: "YouTube Uploader"
        * User support email: your email
        * App logo: (optional, can skip)
        * Application home page: (optional, can skip)
        * Authorized domains: (optional, can skip)
        * Developer contact: your email
        * Click "Save and Continue"
      
      - Scopes:
        * Click "Add or Remove Scopes"
        * Search for "youtube.upload"
        * Check the box for: ".../auth/youtube.upload"
        * Click "Update" then "Save and Continue"
      
      - OAuth Client ID:
        * Application type: Select "Desktop app"
        * Name: "YouTube Upload Client"
        * Click "Create"
      
      - Test users (if app is in testing mode):
        * Click "Add Users"
        * Add your Google email address
        * Save
   
   e) Download Credentials:
      - Click the download icon (⬇️) next to your OAuth 2.0 Client ID
      - Save the file as 'client_secrets.json' in the same directory as this script
   
3. First Time Authentication:
   - Run the upload script
   - A browser window will open
   - Log in with your Google account
   - Grant permissions to upload videos
   - Credentials will be saved to 'youtube_credentials.json' for future use

4. Usage:
   python upload_youtube.py --video /path/to/video.mp4 --title "My Video"
   
   All settings (privacy, playlist, tags) are configured in project_config.json

TROUBLESHOOTING:
================
- "Client secrets file not found" → Complete step 2 above
- "Access Not Configured" → Make sure YouTube Data API v3 is enabled (step 2c)
- "Quota exceeded" → YouTube API has daily limits, wait 24 hours or request increase
- Browser doesn't open → Check firewall, try running on local machine

FILES CREATED:
==============
- client_secrets.json → OAuth credentials (you provide, keep private)
- youtube_credentials.json → Access token (auto-generated, keep private)
- Add both to .gitignore to avoid committing to version control
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
import http.client
import httplib2
import random
import tempfile

# YouTube API imports
try:
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
except ImportError:
    print("ERROR: Missing required packages. Install with:")
    print("  pip install google-api-python-client google-auth-oauthlib google-auth-httplib2")
    sys.exit(1)


# OAuth 2.0 scopes for YouTube upload
# Using youtube.force-ssl for Brand Account compatibility
SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]

# Explicitly tell the underlying HTTP transport library not to retry, since
# we are handling retry logic ourselves.
httplib2.RETRIES = 1

# Maximum number of times to retry before giving up.
MAX_RETRIES = 10

# Always retry when these exceptions are raised.
RETRIABLE_EXCEPTIONS = (httplib2.HttpLib2Error, IOError, http.client.NotConnected,
    http.client.IncompleteRead, http.client.ImproperConnectionState,
    http.client.CannotSendRequest, http.client.CannotSendHeader,
    http.client.ResponseNotReady, http.client.BadStatusLine)

# Always retry when an apiclient.errors.HttpError with one of these status
# codes is raised.
RETRIABLE_STATUS_CODES = [500, 502, 503, 504]


def get_authenticated_service(client_secrets_file, credentials_file):
    """
    Authenticate and return YouTube API service.
    
    Args:
        client_secrets_file: Path to OAuth client secrets JSON
        credentials_file: Path to stored credentials
    
    Returns:
        YouTube API service object
    """
    creds = None
    
    # Load existing credentials
    if Path(credentials_file).exists():
        creds = Credentials.from_authorized_user_file(credentials_file, SCOPES)
    
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("Refreshing expired credentials...")
            creds.refresh(Request())
        else:
            if not Path(client_secrets_file).exists():
                print(f"ERROR: Client secrets file not found: {client_secrets_file}")
                print("\n" + "=" * 70)
                print("📋 OAUTH SETUP REQUIRED")
                print("=" * 70)
                print("\nFollow these steps to get your OAuth credentials:\n")
                print("1. Go to: https://console.cloud.google.com/")
                print("   (Log in with your Google account)\n")
                print("2. Create a new project:")
                print("   - Click 'Select a project' → 'New Project'")
                print("   - Name: 'YouTube Uploader'")
                print("   - Click 'Create'\n")
                print("3. Enable YouTube Data API v3:")
                print("   - Go to 'APIs & Services' → 'Library'")
                print("   - Search: 'YouTube Data API v3'")
                print("   - Click it and press 'Enable'\n")
                print("4. Create OAuth 2.0 credentials:")
                print("   - Go to 'APIs & Services' → 'Credentials'")
                print("   - Click '+ CREATE CREDENTIALS' at top")
                print("   - In the credential wizard:")
                print("     * Select an API: 'YouTube Data API v3'")
                print("     * What data?: Select 'User data'")
                print("     * Click 'Next'\n")
                print("   Configure OAuth consent screen (if needed):")
                print("   - App name: 'YouTube Uploader'")
                print("   - User support email: your email")
                print("   - Developer contact: your email")
                print("   - Save and Continue\n")
                print("   Scopes:")
                print("   - Click 'Add or Remove Scopes'")
                print("   - Search: 'youtube.upload'")
                print("   - Check: '.../auth/youtube.upload'")
                print("   - Update → Save and Continue\n")
                print("   OAuth Client ID:")
                print("   - Application type: Desktop app")
                print("   - Name: 'YouTube Upload Client'")
                print("   - Click 'Create'\n")
                print("   Test users (if in testing mode):")
                print("   - Add your Google email address\n")
                print("5. Download credentials:")
                print("   - Click the download icon (⬇️) next to your client ID")
                print("   - Save as: 'client_secrets.json'")
                print(f"   - Place at: {Path(client_secrets_file).absolute()}\n")
                print("=" * 70)
                print("\nAfter setup, run this command again. A browser will open")
                print("for you to authorize the app. Credentials will be saved for")
                print("future uploads.\n")
                print("For detailed instructions, see: YOUTUBE_UPLOAD_SETUP.md")
                print("=" * 70)
                sys.exit(1)
            
            print("Starting OAuth flow...")
            flow = InstalledAppFlow.from_client_secrets_file(client_secrets_file, SCOPES)
            creds = flow.run_local_server(port=0)
        
        # Save the credentials for the next run
        with open(credentials_file, "w") as token:
            token.write(creds.to_json())
        print(f"✓ Credentials saved to: {credentials_file}")
    
    return build("youtube", "v3", credentials=creds)


def initialize_upload(youtube, video_file, metadata):
    """
    Upload video to YouTube with metadata.
    
    Args:
        youtube: YouTube API service object
        video_file: Path to video file
        metadata: Dictionary with video metadata
    
    Returns:
        Video ID if successful, None otherwise
    """
    body = {
        "snippet": {
            "title": metadata.get("title", "Untitled Video"),
            "description": metadata.get("description", ""),
            "tags": metadata.get("tags", []),
            "categoryId": metadata.get("category_id", "22"),  # 22 = People & Blogs
        },
        "status": {
            "privacyStatus": metadata.get("privacy_status", "private"),
            "selfDeclaredMadeForKids": metadata.get("made_for_kids", False),
            "containsSyntheticMedia": metadata.get("altered_content", False),
        }
    }
    
    # Add playlist if specified
    if metadata.get("playlist_id"):
        body["snippet"]["playlistId"] = metadata["playlist_id"]
    
    # Call the API's videos.insert method to create and upload the video.
    # Use 10MB chunks for large file uploads (better progress tracking and reliability)
    CHUNK_SIZE = 10 * 1024 * 1024  # 10 MB
    
    insert_request = youtube.videos().insert(
        part=",".join(body.keys()),
        body=body,
        media_body=MediaFileUpload(video_file, chunksize=CHUNK_SIZE, resumable=True)
    )
    
    return resumable_upload(insert_request)


def resumable_upload(request):
    """
    Execute upload with resumable upload and retry logic.
    
    Args:
        request: API request object
    
    Returns:
        Video ID if successful, None otherwise
    """
    response = None
    error = None
    retry = 0
    
    print("Uploading file...")
    while response is None:
        try:
            status, response = request.next_chunk()
            if response is not None:
                if "id" in response:
                    video_id = response["id"]
                    print(f"\n✅ Video uploaded successfully!")
                    print(f"Video ID: {video_id}")
                    print(f"URL: https://www.youtube.com/watch?v={video_id}")
                    return video_id
                else:
                    print(f"\nERROR: Upload failed with response: {response}")
                    return None
            elif status:
                progress = int(status.progress() * 100)
                bytes_uploaded = status.resumable_progress
                total_size = status.total_size
                mb_uploaded = bytes_uploaded / (1024 * 1024)
                mb_total = total_size / (1024 * 1024)
                print(f"  Upload progress: {progress}% ({mb_uploaded:.1f} MB / {mb_total:.1f} MB)", end="\r")
        
        except HttpError as e:
            if e.resp.status in RETRIABLE_STATUS_CODES:
                error = f"A retriable HTTP error {e.resp.status} occurred:\n{e.content}"
            else:
                raise
        
        except RETRIABLE_EXCEPTIONS as e:
            error = f"A retriable error occurred: {e}"
        
        if error is not None:
            print(f"\n{error}")
            retry += 1
            if retry > MAX_RETRIES:
                print(f"ERROR: No longer attempting to retry after {MAX_RETRIES} attempts")
                return None
            
            max_sleep = 2 ** retry
            sleep_seconds = random.random() * max_sleep
            print(f"Sleeping {sleep_seconds:.1f} seconds and then retrying...")
            time.sleep(sleep_seconds)


def add_to_playlist(youtube, video_id, playlist_id):
    """
    Add video to a playlist.
    
    Args:
        youtube: YouTube API service object
        video_id: Video ID to add
        playlist_id: Playlist ID to add video to
    
    Returns:
        True if successful, False otherwise
    """
    try:
        youtube.playlistItems().insert(
            part="snippet",
            body={
                "snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {
                        "kind": "youtube#video",
                        "videoId": video_id
                    }
                }
            }
        ).execute()
        print(f"✓ Video added to playlist: {playlist_id}")
        return True
    except HttpError as e:
        print(f"ERROR: Failed to add to playlist: {e}")
        return False


def load_config(config_path):
    """Load project configuration."""
    if Path(config_path).exists():
        with open(config_path, "r") as f:
            return json.load(f)
    return {}


def _find_first_image(folder):
    """Return the most recent image file in a folder, or None."""
    folder = Path(folder)
    if not folder.exists():
        return None
    candidates = []
    for ext in ("*.jpg", "*.jpeg", "*.png", "*.JPG", "*.JPEG", "*.PNG"):
        candidates.extend(folder.glob(ext))
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


def find_asset_thumbnail():
    """Find a thumbnail image, checking assets/photo-index/ first, then assets/photos/."""
    base_dir = Path(__file__).resolve().parent
    for folder in ("photo-index", "photos"):
        result = _find_first_image(base_dir / "assets" / folder)
        if result:
            return result
    return None


def set_thumbnail(youtube, video_id, thumbnail_path):
    """Set a custom thumbnail for a YouTube video."""
    try:
        prepared_path = prepare_thumbnail(thumbnail_path)
        youtube.thumbnails().set(
            videoId=video_id,
            media_body=MediaFileUpload(str(prepared_path))
        ).execute()
        print(f"✓ Thumbnail set: {prepared_path}")
        return True
    except HttpError as e:
        print(f"ERROR: Failed to set thumbnail: {e}")
        return False


def prepare_thumbnail(thumbnail_path):
    """Resize and stretch thumbnail to 1280x720 and keep under 2MB."""
    try:
        from PIL import Image
    except ImportError:
        print("ERROR: Pillow is required for thumbnail processing. Install with: pip install pillow")
        raise

    img = Image.open(thumbnail_path).convert("RGB")
    # Resize to cover 1280x720 while preserving aspect ratio, then center-crop
    target_w, target_h = 1280, 720
    src_w, src_h = img.size
    scale = max(target_w / src_w, target_h / src_h)
    new_w = int(src_w * scale)
    new_h = int(src_h * scale)
    img = img.resize((new_w, new_h), Image.LANCZOS)

    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    right = left + target_w
    bottom = top + target_h
    img = img.crop((left, top, right, bottom))

    tmp_dir = Path(tempfile.gettempdir())
    out_path = tmp_dir / "youtube_thumbnail_upload.jpg"
    img.save(out_path, format="JPEG", quality=85, optimize=True)

    # If still too large, reduce quality
    if out_path.stat().st_size > 2 * 1024 * 1024:
        img.save(out_path, format="JPEG", quality=70, optimize=True)

    return out_path


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Upload video to YouTube channel"
    )
    parser.add_argument(
        "--video",
        type=str,
        required=False,
        help="Path to video file to upload"
    )
    parser.add_argument(
        "--title",
        type=str,
        help="Video title (default: from config or filename)"
    )
    parser.add_argument(
        "--description",
        type=str,
        help="Video description (default: from config)"
    )
    parser.add_argument(
        "--tags",
        type=str,
        help="Comma-separated tags (default: from config)"
    )
    parser.add_argument(
        "--privacy",
        type=str,
        choices=["public", "private", "unlisted"],
        help="Privacy status (default: from config or private)"
    )
    parser.add_argument(
        "--category",
        type=str,
        help="YouTube category ID (default: from config or 22)"
    )
    parser.add_argument(
        "--playlist",
        type=str,
        help="Playlist ID to add video to (default: from config)"
    )
    parser.add_argument(
        "--config",
        type=str,
        default="project_config.json",
        help="Project configuration file (default: project_config.json)"
    )
    parser.add_argument(
        "--client-secrets",
        type=str,
        default="client_secrets.json",
        help="OAuth client secrets file (default: client_secrets.json)"
    )
    parser.add_argument(
        "--credentials",
        type=str,
        default="youtube_credentials.json",
        help="Stored credentials file (default: youtube_credentials.json)"
    )
    parser.add_argument(
        "--thumbnail",
        type=str,
        help="Path to thumbnail image (JPG/PNG). If omitted, uses assets/photos"
    )
    parser.add_argument(
        "--set-thumbnail",
        type=str,
        help="Set thumbnail for an existing video ID (no upload)"
    )
    parser.add_argument(
        "--shorts",
        action="store_true",
        help="Append #shorts to the video title for YouTube Shorts"
    )
    parser.add_argument(
        "--related-video",
        type=str,
        help="Related video ID to reference in the description (for teasers)"
    )
    
    args = parser.parse_args()
    
    # Load config
    config = load_config(args.config)
    youtube_cfg = config.get("youtube", {})

    paths_cfg = config.get("paths", {})

    # Resolve thumbnail (optional): CLI arg > config thumbnail_dir > auto-discovery
    if args.thumbnail:
        thumbnail_path = Path(args.thumbnail)
    elif paths_cfg.get("thumbnail_dir"):
        thumbnail_path = _find_first_image(Path(paths_cfg["thumbnail_dir"]))
    else:
        thumbnail_path = find_asset_thumbnail()
    if thumbnail_path and not thumbnail_path.exists():
        print(f"ERROR: Thumbnail file not found: {thumbnail_path}")
        sys.exit(1)

    # Thumbnail-only mode
    if args.set_thumbnail:
        try:
            youtube = get_authenticated_service(args.client_secrets, args.credentials)
            print("✓ Authenticated with YouTube API\n")
        except Exception as e:
            print(f"ERROR: Authentication failed: {e}")
            sys.exit(1)

        if not thumbnail_path:
            print("ERROR: No thumbnail found. Provide --thumbnail or place images in assets/photos")
            sys.exit(1)

        if set_thumbnail(youtube, args.set_thumbnail, thumbnail_path):
            print("✅ Thumbnail updated")
            sys.exit(0)
        sys.exit(1)
    
    # Check video file exists
    if not args.video:
        print("ERROR: --video is required unless using --set-thumbnail")
        sys.exit(1)

    video_path = Path(args.video)
    if not video_path.exists():
        print(f"ERROR: Video file not found: {video_path}")
        sys.exit(1)
    
    # Prepare metadata
    title = args.title or youtube_cfg.get("upload_title") or youtube_cfg.get("default_title") or video_path.stem
    if args.shorts and "#shorts" not in title.lower():
        title = f"{title} #shorts"

    description = args.description or youtube_cfg.get("default_description", "")

    # Use shorts playlist when uploading Shorts, otherwise default playlist
    if args.playlist:
        playlist_id = args.playlist
    elif args.shorts and youtube_cfg.get("shorts_playlist_id"):
        playlist_id = youtube_cfg["shorts_playlist_id"]
    else:
        playlist_id = youtube_cfg.get("default_playlist_id")

    metadata = {
        "title": title,
        "description": description,
        "tags": (args.tags.split(",") if args.tags else youtube_cfg.get("default_tags", [])),
        "category_id": args.category or youtube_cfg.get("category_id", "22"),
        "privacy_status": args.privacy or youtube_cfg.get("default_privacy", "private"),
        "made_for_kids": youtube_cfg.get("made_for_kids", False),
        "playlist_id": playlist_id,
        "altered_content": youtube_cfg.get("altered_content", False),
    }
    
    print("\n" + "=" * 72)
    print("📤 YouTube Video Upload")
    print("=" * 72)
    print(f"Video:       {video_path}")
    print(f"Title:       {metadata['title']}")
    print(f"Privacy:     {metadata['privacy_status']}")
    print(f"Category:    {metadata['category_id']}")
    print(f"Tags:        {', '.join(metadata['tags'][:5])}{'...' if len(metadata['tags']) > 5 else ''}")
    if thumbnail_path:
        print(f"Thumbnail:  {thumbnail_path}")
    print(f"Altered:     {'Yes' if metadata['altered_content'] else 'No'}")
    
    size_mb = video_path.stat().st_size / (1024 * 1024)
    print(f"Size:        {size_mb:.1f} MB")
    print("=" * 72 + "\n")
    
    # Authenticate
    try:
        youtube = get_authenticated_service(args.client_secrets, args.credentials)
        print("✓ Authenticated with YouTube API\n")
    except Exception as e:
        print(f"ERROR: Authentication failed: {e}")
        sys.exit(1)
    
    # Upload video
    try:
        video_id = initialize_upload(youtube, str(video_path), metadata)
        
        if video_id:
            # Add to playlist if specified and not already added during upload
            if metadata["playlist_id"] and "playlistId" not in metadata.get("snippet", {}):
                add_to_playlist(youtube, video_id, metadata["playlist_id"])

            # Set thumbnail if available
            if thumbnail_path:
                set_thumbnail(youtube, video_id, thumbnail_path)
            
            studio_url = f"https://studio.youtube.com/video/{video_id}/edit"

            print("\n" + "=" * 72)
            print("✅ Upload Complete")
            print("=" * 72)
            print(f"Video ID:    {video_id}")
            print(f"Watch URL:   https://www.youtube.com/watch?v={video_id}")
            print(f"Studio URL:  {studio_url}")
            print("=" * 72)

            if args.related_video:
                print(f"\n⚠️  Set 'Related video' manually in YouTube Studio (not available via API):")
                print(f"   Related video: https://www.youtube.com/watch?v={args.related_video}")
                print(f"   Studio URL:    {studio_url}")
        else:
            print("\n❌ Upload failed")
            sys.exit(1)
    
    except HttpError as e:
        print(f"\n❌ An HTTP error {e.resp.status} occurred:\n{e.content}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Upload error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

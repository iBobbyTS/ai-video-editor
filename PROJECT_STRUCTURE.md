# Project Structure

## Overview
Complete video processing pipeline: AI scene analysis → clip extraction → timeline generation → DaVinci Resolve rendering → YouTube upload.

## Core Pipeline Scripts

### 1. Master Orchestrator
- **`run_pipeline.py`** - Main pipeline that orchestrates all stages
  - Calls analyze_advanced5.py for video analysis
  - Calls extract_scenes.py for clip extraction
  - Calls export_resolve.py for timeline generation
  - Dispatches `event_memory` mode to the lightweight event recap pipeline
  - Usage: `python run_pipeline.py --input /path/to/videos --config project_config.json`

### 2. Analysis Stage
- **`analyze_advanced5.py`** - AI-powered scene analysis
  - Uses ResNet50 for feature extraction
  - Uses Faster R-CNN for object detection
  - Classifies scenes (teaser, intro, work, closing, etc.)
  - Output: JSON files in `analysis_output/`

### 3. Clip Extraction Stage
- **`extract_scenes.py`** - Extracts video clips based on analysis
  - Uses ffmpeg to extract clips at 2x speed
  - Handles audio normalization
  - Output: MKV clips in `ai_clips/{video_name}/`

### 4. Timeline Generation Stage
- **`export_resolve.py`** - Generates FCP XML timeline
  - Creates timeline structure with clips
  - Adds background music and teaser music
  - Handles audio fades and timing
  - Output: `timeline_davinci_resolve.fcpxml`

### 5. Rendering Stage
- **`render_youtube.py`** - Renders timeline in DaVinci Resolve
  - Connects to DaVinci Resolve via API
  - Renders MP4 with H.265 NVIDIA codec
  - Settings: 30 Mbps, 4K resolution
  - Output: `/home/mazsola/Videos/test_renderer.mp4`
  - Usage: `python render_youtube.py --output /path/to/output.mp4`

### 6. Upload Stage
- **`upload_youtube.py`** - Uploads video to YouTube
  - OAuth 2.0 authentication with Brand Account support
  - Supports unlisted/public/private uploads
  - Automatic playlist assignment
  - Usage: `python upload_youtube.py --video /path/to/video.mp4 --title "Title"`

### 7. Utilities
- **`apply_lut_resolve.py`** - Applies LUT to clips in DaVinci Resolve
  - Usage: `python apply_lut_resolve.py --config project_config.json`

### 8. Event Memory Pipeline
- **`event_memory/`** - Deterministic mixed-media recap workflow
  - Scans videos and photos into `media_index.json`
  - Segments videos and still images
  - Merges optional `human_notes.csv`
  - Scores activity/event recap candidates
  - Writes `timeline.json`, `review.md`, and optional `event_memory.fcpxml`
  - Usage: `python run_pipeline.py --mode event_memory --input ./footage --output ./project_output --dry-run --yes`

## Configuration

### `project_config.json`
Central configuration file for all pipeline stages:
```json
{
  "resolve": {
    "render_settings": {
      "format": "mp4",
      "codec": "H265_NVIDIA",
      "video_quality": 30000,
      "output_dir": "/home/mazsola/Videos"
    }
  },
  "youtube": {
    "default_privacy": "unlisted",
    "default_playlist_id": "PL4ML7o5g3fZk0WgDMV1-stbA2lz0bCHFO",
    "category_id": "26"
  }
}
```

## Directory Structure

```
/home/mazsola/video/
├── analyze_advanced5.py          # AI scene analysis
├── extract_scenes.py             # Clip extraction
├── export_resolve.py             # Timeline generation
├── run_pipeline.py               # Master orchestrator
├── render_youtube.py             # DaVinci Resolve rendering
├── upload_youtube.py             # YouTube upload
├── apply_lut_resolve.py          # LUT application utility
├── project_config.json           # Configuration
├── timeline_davinci_resolve.fcpxml  # Generated timeline (ACTIVE)
│
├── ai_clips/                     # Extracted video clips (output)
│   └── {video_name}/            # Per-video clip folders
│
├── analysis_output/              # Scene analysis JSON (output)
│   └── scene_analysis_*.json
│
├── assets/                       # Project assets
│   ├── music/                   # Background/teaser music
│   └── luts/                    # Color grading LUTs
│
├── unused/                       # Old scripts (archived)
├── tools/                        # GPU build/test utilities
│   ├── install_gcc12.sh         # Build GCC 12 for CUDA 12.4
│   ├── patch_cuda_math.sh       # Patch CUDA math headers
│   ├── build_llama_cpp_with_gcc12.sh # Build llama-cpp-python with CUDA
│   └── test_video_gpu.py        # GPU smoke test
│
├── README.md                     # Main documentation
├── README_GPU_COMPILATION.md     # GPU setup guide
└── YOUTUBE_UPLOAD_SETUP.md       # YouTube OAuth setup guide
```

## Typical Workflow

### Full Pipeline (Automated)
```bash
# Run complete pipeline from raw video to timeline
python run_pipeline.py \
  --input /path/to/raw/videos \
  --config project_config.json
```

### Manual Steps (After Pipeline)
```bash
# 1. Import timeline to DaVinci Resolve
# - Open DaVinci Resolve
# - File → Import → Timeline → timeline_davinci_resolve.fcpxml

# 2. Apply LUT to clips
python apply_lut_resolve.py --config project_config.json

# 3. Render video
python render_youtube.py --output /home/mazsola/Videos/output.mp4

# 4. Upload to YouTube
python upload_youtube.py \
  --video /home/mazsola/Videos/output.mp4 \
  --title "Scale Model Build - Part 1" \
  --privacy unlisted
```

## Dependencies

### Python Packages
```bash
pip install torch torchvision opencv-python numpy pillow scikit-learn
pip install google-api-python-client google-auth-oauthlib google-auth-httplib2
```

### External Tools
- **ffmpeg** - Video/audio processing
- **DaVinci Resolve 20** - Video editing and rendering
- **NVIDIA GPU** - H.265 hardware encoding

## Output Files

### Video Processing
- **Raw videos**: User-provided source files
- **Analysis JSON**: `analysis_output/scene_analysis_*.json`
- **Extracted clips**: `ai_clips/{video_name}/*_scene_*.mkv`
- **Timeline XML**: `timeline_davinci_resolve.fcpxml`
- **Rendered video**: `/home/mazsola/Videos/*.mp4`

### YouTube Upload
- **OAuth credentials**: `client_secrets.json` (user-provided)
- **Access token**: `youtube_credentials.json` (auto-generated)
- **Uploaded**: https://www.youtube.com/@modernhackers

## Notes

### Archive Directories
  - **`unused/`** - Old experimental scripts, not used in current pipeline

### Tools
  - **`tools/`** - GPU compilation and verification scripts used by [README_GPU_COMPILATION.md](README_GPU_COMPILATION.md)

### Git Ignore
The following files are excluded from version control:
- `client_secrets.json` - OAuth credentials
- `youtube_credentials.json` - Access tokens
- `*.mp4`, `*.mov`, `*.mkv` - Video files
- `.venv/` - Python virtual environment
- `__pycache__/` - Python bytecode

### Security
- Never commit OAuth credentials to git
- Keep `client_secrets.json` and `youtube_credentials.json` private
- YouTube videos default to unlisted for safety

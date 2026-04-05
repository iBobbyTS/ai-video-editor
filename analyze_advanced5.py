#!/usr/bin/env python3
"""
Advanced AI Video Analysis v5 - TWO-PASS LLM-ENHANCED WORKFLOW
Combines vision models + LLM reasoning for intelligent scene detection:

**PASS 1: Enhanced Metadata Collection**
1. CLIP (ViT-B/32) - Semantic understanding (GPU)
2. ResNet-50 - Deep feature extraction (GPU)
3. Duplicate Detection - Cosine similarity
4. Qwen2.5-VL-7B - Caption ALL frames early (faster 7B model)
5. Caption-based features - Extract keywords, action intensity

**PASS 2: LLM Scene Analysis**
6. LLM Scene Boundaries - Context-aware workflow transitions
7. LLM Classification - Explainable interest ratings
8. LLM Showcases - Diverse moment selection with reasoning

Output: Rich metadata JSON with CLIP + ResNet + captions + LLM decisions
"""

import argparse
import subprocess
import json
import csv
import numpy as np
from pathlib import Path
import cv2
import torch
import torch.nn as nn
import sys
import os
import time
import base64
from contextlib import contextmanager
from torchvision import models, transforms
from torchvision.models import ResNet50_Weights
from torchvision.models.detection import fasterrcnn_resnet50_fpn, FasterRCNN_ResNet50_FPN_Weights
from PIL import Image
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from sklearn.metrics.pairwise import cosine_similarity
import warnings
try:
    from tqdm import tqdm
except Exception:
    tqdm = None
import gc
warnings.filterwarnings('ignore')

# Global callback to suppress llama.cpp verbose output (must persist to avoid garbage collection)
_LLAMA_LOG_CALLBACK = None

# Try to suppress llama.cpp logging as early as possible (before any models are loaded)
try:
    from llama_cpp import llama_log_set
    import ctypes
    
    # Create callback that suppresses all llama.cpp C++ level logging
    _LLAMA_LOG_CALLBACK = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p)(
        lambda level, text, user_data: None
    )
    llama_log_set(_LLAMA_LOG_CALLBACK, ctypes.c_void_p())
except Exception:
    pass  # Silently fail if llama-cpp-python not installed yet

# Context manager to suppress BOTH stdout and stderr at OS level (for C++ verbose output)
import contextlib

@contextlib.contextmanager
def suppress_cpp_output():
    """Redirect both stdout and stderr to /dev/null at OS level to suppress all C++ verbose output."""
    import sys, os
    
    # Save original file descriptors
    stdout_fd = sys.stdout.fileno()
    stderr_fd = sys.stderr.fileno()
    old_stdout = os.dup(stdout_fd)
    old_stderr = os.dup(stderr_fd)
    devnull = os.open(os.devnull, os.O_WRONLY)
    
    try:
        # Redirect both stdout and stderr to /dev/null
        os.dup2(devnull, stdout_fd)
        os.dup2(devnull, stderr_fd)
        sys.stdout.flush()
        sys.stderr.flush()
        yield
    finally:
        # Restore original file descriptors
        sys.stdout.flush()
        sys.stderr.flush()
        os.dup2(old_stdout, stdout_fd)
        os.dup2(old_stderr, stderr_fd)
        os.close(devnull)
        os.close(old_stdout)
        os.close(old_stderr)

# Configuration
VIDEO_FILE = "IMG_3839.MOV"
SAMPLE_INTERVAL = 2
OUTPUT_FILE = "scene_analysis_advanced4.json"
TARGET_OUTPUT_RATIO = 0.15  # Aim for ~10-15% output duration
MAX_SPEED_MULTIPLIER = 8.0

KEYFRAME_PROMPTS = [
    {"key": "applying", "text": "applying polishing compound to model car surface"},
    {"key": "shine", "text": "visible reflection improvement and shine"},
    {"key": "detail", "text": "close-up macro of surface detail and texture"},
    {"key": "hands", "text": "hands working with polishing tool or cloth"},
    {"key": "angle", "text": "changing angle or view of the model car"},
    {"key": "comparison", "text": "before and after surface comparison"},
    {"key": "repetitive", "text": "repetitive unchanging polishing circular motion"},
    {"key": "static", "text": "static scene with no visible activity or change"},
    {"key": "blurry", "text": "blurry out of focus unclear image"}
]

COCO_INSTANCE_CATEGORY_NAMES = [
    "__background__", "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train",
    "truck", "boat", "traffic light", "fire hydrant", "stop sign", "parking meter", "bench",
    "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe",
    "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard",
    "sports ball", "kite", "baseball bat", "baseball glove", "skateboard", "surfboard",
    "tennis racket", "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl",
    "banana", "apple", "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza",
    "donut", "cake", "chair", "couch", "potted plant", "bed", "dining table", "toilet",
    "tv", "laptop", "mouse", "remote", "keyboard", "cell phone", "microwave", "oven",
    "toaster", "sink", "refrigerator", "book", "clock", "vase", "scissors", "teddy bear",
    "hair drier", "toothbrush"
]

@contextmanager
def suppress_stdout_stderr():
    """Context manager to suppress stdout and stderr."""
    null_fds = [os.open(os.devnull, os.O_RDWR) for _ in range(2)]
    save_fds = [os.dup(1), os.dup(2)]
    os.dup2(null_fds[0], 1)
    os.dup2(null_fds[1], 2)
    try:
        yield
    finally:
        os.dup2(save_fds[0], 1)
        os.dup2(save_fds[1], 2)
        for fd in null_fds + save_fds:
            os.close(fd)

print("🚀 Advanced AI Video Analysis v5 - TWO-PASS LLM WORKFLOW")
print("=" * 70)
print("Pass 1: CLIP + ResNet + Qwen2.5-VL captions + metadata")
print("Pass 2: LLM scene detection + classification + showcases")
print("=" * 70)

# Check GPU
if torch.cuda.is_available():
    GPU_DEVICE = 'cuda'
    GPU_NAME = torch.cuda.get_device_name(0)
    GPU_MEMORY = torch.cuda.get_device_properties(0).total_memory / 1024**3
    print(f"\n💎 GPU: {GPU_NAME} ({GPU_MEMORY:.1f}GB)")
    print(f"   CUDA: {torch.version.cuda}, PyTorch: {torch.__version__}")
else:
    GPU_DEVICE = 'cpu'
    print("\n⚠️  No GPU - using CPU (will be slow)")


def get_video_info(video_path):
    """Get video metadata"""
    cmd = ['ffprobe', '-v', 'error', '-select_streams', 'v:0',
           '-show_entries', 'stream=width,height,r_frame_rate,duration',
           '-of', 'json', str(video_path)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    data = json.loads(result.stdout)
    stream = data['streams'][0]
    
    fps_str = stream['r_frame_rate'].split('/')
    fps = int(fps_str[0]) / int(fps_str[1])
    
    return {
        'duration': float(stream['duration']),
        'width': int(stream['width']),
        'height': int(stream['height']),
        'fps': fps
    }


def load_project_config(config_path):
    if not config_path:
        return {}
    path = Path(config_path)
    if not path.exists():
        return {}
    try:
        with open(path, 'r') as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        return {}
    return {}


def load_clip_model():
    """Load CLIP for semantic scene understanding"""
    print("\n🤖 Loading CLIP vision-language model...")
    try:
        import clip
        
        model, preprocess = clip.load("ViT-B/32", device=GPU_DEVICE)
        model.eval()
        
        mem = torch.cuda.memory_allocated(0) / 1024**3 if GPU_DEVICE == 'cuda' else 0
        print(f"   ✓ CLIP ViT-B/32 loaded on {GPU_DEVICE.upper()}")
        print(f"   GPU memory: {mem:.2f}GB")
        
        return {'model': model, 'preprocess': preprocess, 'clip': clip}
    except ImportError:
        print("   ⚠️  CLIP not installed")
        print("   Install: pip install git+https://github.com/openai/CLIP.git")
        return None


def load_resnet_model():
    """Load ResNet-50 for deep feature extraction"""
    print("\n🤖 Loading ResNet-50 feature extractor...")
    
    try:
        weights = ResNet50_Weights.IMAGENET1K_V2
    except Exception:
        weights = ResNet50_Weights.IMAGENET1K_V1
    
    try:
        model = models.resnet50(weights=weights)
        model = nn.Sequential(*list(model.children())[:-1])  # Remove classifier
        model = model.to(GPU_DEVICE)
        model.eval()
        
        transform = weights.transforms()
        
        mem = torch.cuda.memory_allocated(0) / 1024**3 if GPU_DEVICE == 'cuda' else 0
        print(f"   ✓ ResNet-50 loaded on {GPU_DEVICE.upper()} (ImageNet weights)")
        print(f"   GPU memory: {mem:.2f}GB")
        
        return {'model': model, 'transform': transform}
    except Exception as e:
        print(f"   ⚠️  ResNet-50 failed to load with weights: {e}")
        
        try:
            model = models.resnet50(weights=None)
            model = nn.Sequential(*list(model.children())[:-1])
            model = model.to(GPU_DEVICE)
            model.eval()
            
            transform = transforms.Compose([
                transforms.Resize(256),
                transforms.CenterCrop(224),
                transforms.ToTensor(),
                transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
            ])
            
            mem = torch.cuda.memory_allocated(0) / 1024**3 if GPU_DEVICE == 'cuda' else 0
            print(f"   ✓ ResNet-50 loaded on {GPU_DEVICE.upper()} (random weights)")
            print(f"   GPU memory: {mem:.2f}GB")
            
            return {'model': model, 'transform': transform}
        except Exception as e2:
            print(f"   ⚠️  ResNet-50 failed to load: {e2}")
            return None


def load_llava_model():
    """Load Qwen2.5-VL-7B GGUF model using llama-cpp-python with GPU acceleration."""
    print("\n🤖 Loading Qwen2.5-VL-7B GGUF (llama-cpp-python + GPU)...")
    try:
        from llama_cpp import Llama
        from llama_cpp.llama_chat_format import Llava16ChatHandler
        from pathlib import Path
    except ImportError:
        print("   ⚠️  llama-cpp-python not installed")
        print("   Install: pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124")
        return None

    try:
        # Auto-detect downloaded GGUF model in HuggingFace cache
        cache_dir = Path.home() / ".cache/huggingface/hub"
        
        # Qwen2.5-VL-7B-Q4_K_M
        model_pattern = "models--unsloth--Qwen2.5-VL-7B-Instruct-GGUF/**/Qwen2.5-VL-7B-Instruct-Q4_K_M.gguf"
        mmproj_pattern = "models--unsloth--Qwen2.5-VL-7B-Instruct-GGUF/**/mmproj-F16.gguf"
        
        model_matches = list(cache_dir.glob(model_pattern))
        mmproj_matches = list(cache_dir.glob(mmproj_pattern))
        
        if not model_matches or not mmproj_matches:
            print("   ⚠️  Qwen2.5-VL model not found. Download with:")
            print('   python -c "from huggingface_hub import hf_hub_download; hf_hub_download(repo_id=\'unsloth/Qwen2.5-VL-7B-Instruct-GGUF\', filename=\'Qwen2.5-VL-7B-Instruct-Q4_K_M.gguf\'); hf_hub_download(repo_id=\'unsloth/Qwen2.5-VL-7B-Instruct-GGUF\', filename=\'mmproj-F16.gguf\')"')
            return None
        
        model_path = str(model_matches[0])
        mmproj_path = str(mmproj_matches[0])
        
        print(f"   Model: Qwen2.5-VL-7B-Q4_K_M (7B parameters)")
        
        # Load with full GPU acceleration
        start = time.time()
        
        # Load Qwen2.5-VL model with vision projector (uses same handler as LLaVA)
        chat_handler = Llava16ChatHandler(clip_model_path=mmproj_path, verbose=False)
        llm = Llama(
            model_path=model_path,
            chat_handler=chat_handler,
            n_gpu_layers=-1,
            n_ctx=4096,  # 4096 for Qwen2.5-VL
            n_threads=8,
            verbose=False,
            logits_all=False,
        )
        
        load_time = time.time() - start
        
        print(f"   ✓ Qwen2.5-VL-7B GGUF loaded on GPU in {load_time:.1f}s")
        print(f"   Context: 4096 tokens, All layers on GPU")
        
        return {
            "model": llm,
            "model_name": "Qwen2.5-VL-7B",
            "device": "cuda",
        }
    except Exception as e:
        print(f"   ⚠️  Qwen2.5-VL failed to load: {e}")
        import traceback
        traceback.print_exc()
        return None


def clear_cuda_cache():
    gc.collect()
    if torch.cuda.is_available():
        try:
            torch.cuda.synchronize()
        except Exception:
            pass
        torch.cuda.empty_cache()
        try:
            torch.cuda.ipc_collect()
        except Exception:
            pass


def release_model_dict(model_dict):
    """Release model from memory, handling both PyTorch and llama-cpp-python models."""
    if not model_dict:
        return
    try:
        model = model_dict.get("model")
        if model is not None:
            # Special handling for llama-cpp-python Llama objects
            try:
                # Check if it's a Llama object (has ctx attribute from llama.cpp)
                if hasattr(model, 'ctx') and model.ctx is not None:
                    # llama-cpp-python models need explicit context cleanup
                    try:
                        # Free the C++ context to prevent memory leaks
                        model.close()
                    except Exception:
                        pass
                    # Also try to free the chat handler if it exists
                    try:
                        if hasattr(model, 'chat_handler') and model.chat_handler is not None:
                            del model.chat_handler
                    except Exception:
                        pass
            except Exception:
                pass
            del model
    except Exception:
        pass
    try:
        for k in list(model_dict.keys()):
            model_dict[k] = None
    except Exception:
        pass
    # Force garbage collection to ensure C++ objects are destroyed
    gc.collect()


def caption_all_frames_early(frames, llava_model, max_length=40, skip_duplicates=False):
    """Caption ALL frames early for Pass 2 LLM analysis.
    
    Args:
        skip_duplicates: If True, skip captioning frames marked as duplicates (saves time)
    """
    if llava_model is None:
        for frame in frames:
            frame["caption"] = None
        return frames

    model = llava_model["model"]
    model_name = llava_model.get("model_name", "LLaVA")
    
    total_frames = len(frames)
    frames_to_caption = [f for f in frames if not (skip_duplicates and f.get('is_duplicate', False))]
    
    print(f"\n📝 [PASS 1] Captioning {'non-duplicate' if skip_duplicates else 'ALL'} {len(frames_to_caption)} frames with {model_name}")
    if skip_duplicates:
        print(f"   (Skipping {total_frames - len(frames_to_caption)} duplicate frames for speed)")
    print(f"   This provides rich context for LLM scene analysis...")
    
    start_time = time.time()
    
    # Caption prompt optimized for keywords (fast, concise)
    cap_prompt = "List comma-separated keywords only. NO sentences. Example: hands, blue gloves, red car, screwdriver, workbench"
    
    for idx, frame in enumerate(frames_to_caption):
        img = Image.open(frame["path"]).convert("RGB")
        
        # Encode image to base64
        import io
        buffered = io.BytesIO()
        img.save(buffered, format="JPEG")
        image_data = base64.b64encode(buffered.getvalue()).decode('utf-8')
        
        # Single-frame captioning with LLaVA
        try:
            with suppress_cpp_output():
                output = model.create_chat_completion(
                    messages=[
                        {"role": "system", "content": "You output ONLY comma-separated keywords describing visible objects, colors, and actions. NO full sentences. NO descriptions."},
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": cap_prompt},
                                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}}
                            ]
                        }
                    ],
                    max_tokens=max_length,
                    temperature=0.3,
                )
            
            caption = output['choices'][0]['message']['content'].strip()
        except Exception as e:
            print(f"\n   ⚠️  Caption failed for frame {idx+1}: {e}")
            import traceback
            traceback.print_exc()
            caption = "error"
        frame["caption"] = caption
        frame["caption_model"] = model_name
        frame["caption_prompt"] = cap_prompt
        
        # Progress update
        if (idx + 1) % 10 == 0 or (idx + 1) == total_frames:
            elapsed = time.time() - start_time
            avg_time = elapsed / (idx + 1)
            remaining = (total_frames - idx - 1) * avg_time
            eta_min = int(remaining / 60)
            eta_sec = int(remaining % 60)
            
            print(f"\r   Progress: {idx+1}/{len(frames_to_caption)} | "
                  f"Avg: {avg_time:.1f}s/frame | ETA: {eta_min}m {eta_sec}s | "
                  f"Caption: {caption[:50]}...", end="", flush=True)
    
    # Copy captions from original to duplicates
    if skip_duplicates:
        for frame in frames:
            if frame.get('is_duplicate') and not frame.get('caption'):
                # Find similar frame and copy caption
                for other in frames:
                    if other.get('caption') and not other.get('is_duplicate'):
                        # Use first non-duplicate caption as fallback
                        frame['caption'] = other['caption'] + " (duplicate frame)"
                        break
    
    print()  # New line after progress
    total_time = time.time() - start_time
    print(f"   ✓ Captioning complete: {len(frames_to_caption)} frames in {total_time/60:.1f}m")
    return frames


def extract_caption_features(frames):
    """Extract semantic features from captions for LLM analysis."""
    print("\n🔍 Extracting caption-based features...")
    
    # Define keywords to detect in captions
    work_keywords = ['applying', 'polishing', 'brushing', 'painting', 'sanding', 'assembling', 'gluing']
    tool_keywords = ['brush', 'airbrush', 'tweezers', 'tool', 'sandpaper', 'file']
    part_keywords = ['body', 'chassis', 'wheel', 'seat', 'hood', 'door', 'panel', 'decal']
    quality_keywords = ['shine', 'gloss', 'reflection', 'smooth', 'detail', 'texture']
    negative_keywords = ['blurry', 'unclear', 'out of focus', 'static', 'repetitive']
    
    for frame in frames:
        caption = (frame.get("caption") or "").lower()
        
        # Keyword detection
        frame["caption_work_detected"] = any(kw in caption for kw in work_keywords)
        frame["caption_tool_detected"] = any(kw in caption for kw in tool_keywords)
        frame["caption_part_detected"] = any(kw in caption for kw in part_keywords)
        frame["caption_quality_detected"] = any(kw in caption for kw in quality_keywords)
        frame["caption_negative_detected"] = any(kw in caption for kw in negative_keywords)
        
        # Action intensity (word count as proxy for activity)
        word_count = len(caption.split())
        frame["caption_word_count"] = word_count
        frame["caption_action_intensity"] = min(1.0, word_count / 30.0)  # Normalize to 0-1
    
    print(f"   ✓ Caption features extracted for {len(frames)} frames")
    return frames


def extract_frames_parallel(video_path, interval=2):
    """Extract frames in parallel"""
    print(f"\n📹 Extracting frames from: {video_path}")
    
    info = get_video_info(video_path)
    duration = info['duration']
    
    print(f"   Duration: {duration:.1f}s ({duration/60:.1f} min)")
    print(f"   Resolution: {info['width']}x{info['height']} @ {info['fps']:.1f}fps")
    
    video_stem = Path(video_path).stem
    frames_dir = Path.cwd() / "tmp_advanced4_frames" / video_stem
    frames_dir.mkdir(parents=True, exist_ok=True)
    
    timestamps = np.arange(0, duration, interval)
    total = len(timestamps)
    
    print(f"   Extracting {total} frames in parallel (24 workers)...")
    print(f"   [Progress will update every 100 frames]")
    
    def extract_frame(i, ts):
        output_path = frames_dir / f"frame_{i:04d}.jpg"
        # Rotate 90° clockwise (transpose=2), then scale to 1280x720
        cmd = ['ffmpeg', '-y', '-ss', str(ts), '-i', str(video_path),
               '-vframes', '1', '-vf', 'transpose=2,scale=1280:720', '-q:v', '1', str(output_path)]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {'index': i, 'timestamp': ts, 'path': str(output_path)}
    
    frames = [None] * total
    completed = 0
    lock = Lock()
    start_time = time.time()
    
    with ThreadPoolExecutor(max_workers=24) as executor:
        futures = {executor.submit(extract_frame, i, ts): i for i, ts in enumerate(timestamps)}
        
        for future in as_completed(futures):
            frame = future.result()
            frames[frame['index']] = frame
            completed += 1
            if completed % 100 == 0 or completed == total:
                with lock:
                    elapsed = time.time() - start_time
                    speed = completed / elapsed if elapsed > 0 else 0
                    remaining = (total - completed) / speed if speed > 0 else 0
                    print(f"      Progress: {completed}/{total} frames | Speed: {speed:.1f} fps | ETA: {int(remaining/60)}m {int(remaining%60)}s")
    
    print(f"   ✓ Extracted {total} frames")
    return frames


def compute_dhash(image_path, hash_size=8):
    """Compute perceptual dHash for an image file."""
    img = cv2.imread(str(image_path))
    if img is None:
        return None
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    resized = cv2.resize(gray, (hash_size + 1, hash_size))
    diff = resized[:, 1:] > resized[:, :-1]
    bits = ''.join('1' if v else '0' for v in diff.flatten())
    return f"{int(bits, 2):0{hash_size * hash_size // 4}x}"


def analyze_with_clip(frames, clip_model):
    """GPU-accelerated CLIP semantic analysis"""
    print(f"\n🔍 [PASS 1 - 1/3] CLIP semantic analysis on GPU...")
    
    if clip_model is None:
        print("   ⚠️  CLIP not available, skipping")
        for f in frames:
            f['semantic_interest'] = 0.5
            f['semantic_boring'] = 0.5
        return frames
    
    # Define semantic prompts for SCALE MODEL workflow detection
    text_prompts = [p["text"] for p in KEYFRAME_PROMPTS]
    
    model = clip_model['model']
    preprocess = clip_model['preprocess']
    clip = clip_model['clip']
    
    mem_start = torch.cuda.memory_allocated(0) / 1024**3 if GPU_DEVICE == 'cuda' else 0
    
    with torch.no_grad():
        # Encode text prompts once
        text_tokens = clip.tokenize(text_prompts).to(GPU_DEVICE)
        text_features = model.encode_text(text_tokens)
        text_features = text_features / text_features.norm(dim=-1, keepdim=True)
        
        # Batch process images
        BATCH_SIZE = 32
        for batch_start in range(0, len(frames), BATCH_SIZE):
            batch_end = min(batch_start + BATCH_SIZE, len(frames))
            batch_images = []
            
            for i in range(batch_start, batch_end):
                img = Image.open(frames[i]['path']).convert('RGB')
                img_tensor = preprocess(img)
                batch_images.append(img_tensor)
            
            # GPU inference
            batch_tensor = torch.stack(batch_images).to(GPU_DEVICE)
            image_features = model.encode_image(batch_tensor)
            image_features = image_features / image_features.norm(dim=-1, keepdim=True)
            
            # Calculate semantic similarity
            similarity = (100.0 * image_features @ text_features.T).softmax(dim=-1)
            scores = similarity.cpu().numpy()
            
            # Also store raw CLIP image features for visual similarity comparison
            for i, img_feat in enumerate(image_features):
                idx = batch_start + i
                frames[idx]['clip_image_features'] = img_feat.cpu().numpy()
            
            for i, score in enumerate(scores):
                idx = batch_start + i
                frames[idx]['clip_applying'] = float(score[0])
                frames[idx]['clip_shine'] = float(score[1])
                frames[idx]['clip_detail'] = float(score[2])
                frames[idx]['clip_hands'] = float(score[3])
                frames[idx]['clip_angle'] = float(score[4])
                frames[idx]['clip_comparison'] = float(score[5])
                frames[idx]['clip_repetitive'] = float(score[6])
                frames[idx]['clip_static'] = float(score[7])
                frames[idx]['clip_blurry'] = float(score[8])
                
                # Combined interest
                interest_raw = (score[0] * 0.30 + score[1] * 0.20 + score[2] * 0.20 + 
                               score[3] * 0.20 + score[4] * 0.07 + score[5] * 0.03)
                boring = score[6] * 0.55 + score[7] * 0.35 + score[8] * 0.10
                interest = max(0.0, interest_raw - (boring * 0.50))
                frames[idx]['semantic_interest'] = float(interest)
                frames[idx]['semantic_boring'] = float(boring)
            
            if batch_end % 64 == 0:
                mem = torch.cuda.memory_allocated(0) / 1024**3 if GPU_DEVICE == 'cuda' else 0
                print(f"      {batch_end}/{len(frames)} frames (GPU: {mem:.2f}GB)")
    
    mem_peak = torch.cuda.max_memory_allocated(0) / 1024**3 if GPU_DEVICE == 'cuda' else 0
    print(f"   ✓ CLIP complete (peak GPU: {mem_peak:.2f}GB)")
    if GPU_DEVICE == 'cuda':
        torch.cuda.reset_peak_memory_stats()
    
    return frames


def extract_features(frames, resnet_model):
    """GPU-accelerated deep feature extraction"""
    print(f"\n🔍 [PASS 1 - 2/3] ResNet-50 feature extraction on GPU...")
    
    if resnet_model is None:
        print("   ⚠️  ResNet not available, using basic features")
        features_list = []
        for frame in frames:
            img = cv2.imread(frame['path'])
            hist = cv2.calcHist([img], [0, 1, 2], None, [8, 8, 8], [0, 256, 0, 256, 0, 256])
            hist = hist.flatten() / hist.sum()
            frame['features'] = hist
            features_list.append(hist)
        print(f"   ✓ Basic feature extraction complete")
        return frames, np.array(features_list)
    
    model = resnet_model['model']
    transform = resnet_model['transform']
    
    features_list = []
    
    with torch.no_grad():
        BATCH_SIZE = 64
        for batch_start in range(0, len(frames), BATCH_SIZE):
            batch_end = min(batch_start + BATCH_SIZE, len(frames))
            batch_images = []
            
            for i in range(batch_start, batch_end):
                img = Image.open(frames[i]['path']).convert('RGB')
                img_tensor = transform(img)
                batch_images.append(img_tensor)
            
            # GPU batch inference
            batch_tensor = torch.stack(batch_images).to(GPU_DEVICE)
            features = model(batch_tensor)
            features = features.squeeze().cpu().numpy()
            
            if features.ndim == 1:
                features = features.reshape(1, -1)
            
            for i, feat in enumerate(features):
                frames[batch_start + i]['features'] = feat
                features_list.append(feat)
            
            if batch_end % 128 == 0:
                mem = torch.cuda.memory_allocated(0) / 1024**3 if GPU_DEVICE == 'cuda' else 0
                print(f"      {batch_end}/{len(frames)} frames (GPU: {mem:.2f}GB)")
    
    print(f"   ✓ Feature extraction complete")
    return frames, np.array(features_list)


def detect_duplicates_and_repetition(frames, features):
    """Detect duplicate and repetitive scenes using CLIP semantic + visual similarity"""
    print(f"\n🔍 [PASS 1 - 3/3] Detecting duplicates and motion analysis...")
    
    # Build CLIP semantic feature matrix
    clip_features = []
    for f in frames:
        feat = [
            f.get('clip_applying', 0),
            f.get('clip_shine', 0),
            f.get('clip_detail', 0),
            f.get('clip_hands', 0),
            f.get('clip_angle', 0),
            f.get('clip_comparison', 0),
            f.get('clip_repetitive', 0),
            f.get('clip_static', 0),
            f.get('clip_blurry', 0),
        ]
        clip_features.append(feat)
    
    clip_features = np.array(clip_features)
    clip_similarity = cosine_similarity(clip_features)
    
    # Visual similarity from ResNet features
    feature_similarity = cosine_similarity(features) if features is not None else clip_similarity
    
    # Combine semantic + visual similarity
    combined_similarity = (clip_similarity * 0.6) + (feature_similarity * 0.4)
    
    # Calculate motion between consecutive frames
    print("   Analyzing motion and semantic similarity...")
    for i in range(len(frames)):
        # Motion detection
        if i < len(frames) - 1:
            motion = 1.0 - combined_similarity[i, i + 1]
            frames[i]['motion'] = float(motion)
        else:
            frames[i]['motion'] = 0.0
        
        # Check similarity to nearby frames
        window_start = max(0, i - 5)
        window_end = min(len(frames), i + 6)
        
        similarities = combined_similarity[i, window_start:window_end]
        similarities = np.delete(similarities, i - window_start)
        
        max_sim = np.max(similarities) if len(similarities) > 0 else 0
        avg_sim = np.mean(similarities) if len(similarities) > 0 else 0
        
        frames[i]['max_similarity'] = float(max_sim)
        frames[i]['avg_similarity'] = float(avg_sim)
        
        frames[i]['is_duplicate'] = (max_sim > 0.975 and frames[i]['motion'] < 0.03)
        frames[i]['is_repetitive'] = (avg_sim > 0.93 and frames[i]['motion'] < 0.06)
        
        if (i + 1) % 100 == 0:
            print(f"      {i+1}/{len(frames)} frames analyzed")
    
    duplicates = sum(1 for f in frames if f['is_duplicate'])
    repetitive = sum(1 for f in frames if f['is_repetitive'])
    avg_motion = np.mean([f['motion'] for f in frames[:-1]])
    
    print(f"   ✓ Found {duplicates} duplicate, {repetitive} repetitive frames")
    print(f"   ✓ Average motion: {avg_motion:.3f}")
    return frames


def save_metadata_json(frames, output_path, video_name):
    """Save rich metadata JSON for Pass 2 LLM analysis."""
    print(f"\n💾 Saving rich metadata for LLM analysis...")
    
    # Prepare frame metadata (exclude heavy numpy arrays)
    frame_metadata = []
    for frame in frames:
        metadata = {
            'index': int(frame['index']),
            'timestamp': float(frame['timestamp']),
            'path': frame['path'],
            'caption': frame.get('caption'),
            'caption_work_detected': bool(frame.get('caption_work_detected', False)),
            'caption_tool_detected': bool(frame.get('caption_tool_detected', False)),
            'caption_part_detected': bool(frame.get('caption_part_detected', False)),
            'caption_quality_detected': bool(frame.get('caption_quality_detected', False)),
            'caption_negative_detected': bool(frame.get('caption_negative_detected', False)),
            'caption_word_count': int(frame.get('caption_word_count', 0)),
            'caption_action_intensity': float(frame.get('caption_action_intensity', 0.0)),
            'clip_applying': float(frame.get('clip_applying', 0.0)),
            'clip_shine': float(frame.get('clip_shine', 0.0)),
            'clip_detail': float(frame.get('clip_detail', 0.0)),
            'clip_hands': float(frame.get('clip_hands', 0.0)),
            'clip_angle': float(frame.get('clip_angle', 0.0)),
            'clip_comparison': float(frame.get('clip_comparison', 0.0)),
            'clip_repetitive': float(frame.get('clip_repetitive', 0.0)),
            'clip_static': float(frame.get('clip_static', 0.0)),
            'clip_blurry': float(frame.get('clip_blurry', 0.0)),
            'semantic_interest': float(frame.get('semantic_interest', 0.0)),
            'semantic_boring': float(frame.get('semantic_boring', 0.0)),
            'motion': float(frame.get('motion', 0.0)),
            'max_similarity': float(frame.get('max_similarity', 0.0)),
            'avg_similarity': float(frame.get('avg_similarity', 0.0)),
            'is_duplicate': bool(frame.get('is_duplicate', False)),
            'is_repetitive': bool(frame.get('is_repetitive', False)),
        }
        frame_metadata.append(metadata)
    
    metadata = {
        'video': video_name,
        'method': 'advanced_ai_v4_pass1',
        'total_frames': len(frames),
        'frames': frame_metadata,
    }
    
    with open(output_path, 'w') as f:
        json.dump(metadata, f, indent=2)
    
    print(f"   ✓ Metadata saved: {output_path}")
    return metadata


def llm_detect_scene_boundaries(frames, llava_model):
    """Use LLM to detect scene boundaries based on captions + metrics."""
    print(f"\n🤖 [PASS 2 - 1/3] LLM detecting scene boundaries...")
    
    if llava_model is None:
        print("   ⚠️  LLaVA not available, falling back to rule-based")
        return fallback_scene_boundaries(frames)
    
    model = llava_model["model"]
    
    # Prepare context for LLM (sample every 20s to avoid token limits)
    sample_interval = 20  # seconds
    sampled_frames = []
    for frame in frames:
        if frame['timestamp'] % sample_interval < 2:  # Within 2s of sample point
            sampled_frames.append(frame)
    
    # Build context summary
    context_parts = []
    for frame in sampled_frames[:30]:  # Limit to first 30 samples to fit context window
        ts = frame['timestamp']
        caption = frame.get('caption', 'no caption')
        interest = frame.get('semantic_interest', 0)
        motion = frame.get('motion', 0)
        context_parts.append(f"{ts:.0f}s: {caption} (interest={interest:.2f}, motion={motion:.3f})")
    
    context_text = "\n".join(context_parts)
    
    # Ask LLM to identify scene transitions
    prompt = f"""Analyze this scale model building video timeline. Identify major scene transitions where the workflow changes significantly (e.g., switching parts, different work phases, camera angle changes).

Timeline:
{context_text}

List scene boundary timestamps where meaningful transitions occur. Format: "XXs: reason"
Keep it concise (max 5 boundaries)."""
    
    try:
        with suppress_cpp_output():
            output = model.create_chat_completion(
                messages=[
                    {"role": "system", "content": "You are an expert video analyst specializing in hobby workshop videos."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=200,
                temperature=0.2,
            )
        
        response = output['choices'][0]['message']['content'].strip()
        print(f"   LLM response:\n{response}")
        
        # Parse timestamps from response
        transitions = [0]  # Always start at 0
        import re
        for match in re.finditer(r'(\d+)s:', response):
            ts = int(match.group(1))
            if ts > 0 and ts not in transitions:
                transitions.append(ts)
        
        transitions.sort()
        transitions.append(frames[-1]['timestamp'])  # Always end at last frame
        
        print(f"   ✓ LLM detected {len(transitions)-1} scene boundaries")
        return transitions
        
    except Exception as e:
        print(f"   ⚠️  LLM scene detection failed: {e}")
        return fallback_scene_boundaries(frames)


def fallback_scene_boundaries(frames):
    """Fallback rule-based scene boundary detection."""
    print("   Using rule-based scene detection (fallback)")
    
    WINDOW_SIZE = 8
    transitions = [0]
    
    for i in range(WINDOW_SIZE, len(frames), WINDOW_SIZE):
        if i + WINDOW_SIZE >= len(frames):
            break
        
        prev_window = frames[i-WINDOW_SIZE:i]
        curr_window = frames[i:i+WINDOW_SIZE]
        
        prev_interest = np.mean([f['semantic_interest'] for f in prev_window])
        curr_interest = np.mean([f['semantic_interest'] for f in curr_window])
        
        prev_motion = np.mean([f.get('motion', 0) for f in prev_window])
        curr_motion = np.mean([f.get('motion', 0) for f in curr_window])
        
        interest_change = abs(curr_interest - prev_interest)
        motion_change = abs(curr_motion - prev_motion)
        
        if interest_change > 0.05 or motion_change > 0.03:
            transitions.append(frames[i]['timestamp'])
    
    transitions.append(frames[-1]['timestamp'])
    print(f"   ✓ Detected {len(transitions)-1} scene boundaries")
    return transitions


def llm_classify_scenes(scenes, frames_by_scene, llava_model, mode='build'):
    """Use LLM to classify scenes with explainable reasoning."""
    print(f"\n🤖 [PASS 2 - 2/3] LLM classifying scenes (mode={mode})...")
    
    if llava_model is None:
        print("   ⚠️  LLaVA not available, falling back to rule-based")
        return fallback_classify_scenes(scenes, mode=mode)
    
    is_unboxing = (mode == 'unboxing')
    model = llava_model["model"]
    
    for scene in scenes:
        scene_id = scene['scene_num'] - 1
        scene_frames = frames_by_scene.get(scene_id, [])
        
        if not scene_frames:
            scene['classification'] = 'low'
            scene['speed'] = 1.0 if is_unboxing else 4.0
            scene['llm_reasoning'] = 'No frames available'
            continue
        
        # Sample captions from this scene
        sample_captions = []
        for i in range(min(5, len(scene_frames))):
            idx = int(i * len(scene_frames) / 5)
            caption = scene_frames[idx].get('caption', 'no caption')
            sample_captions.append(caption)
        
        captions_text = "\n".join([f"- {c}" for c in sample_captions])
        
        # Get scene metrics
        avg_interest = scene.get('semantic_interest', 0)
        dup_ratio = scene.get('duplicate_ratio', 0)
        
        # Ask LLM to rate this scene - prompt differs by mode
        if is_unboxing:
            prompt = f"""Rate this {scene['duration']:.0f}s video scene from a scale model kit unboxing/showcase on interest level (1-10).

IMPORTANT: For unboxing/showcase content, highly value:
- Revealing box contents, sprues, parts, decals, and instructions
- Narration explaining features, quality, or comparisons
- Close-up shots of mold quality, part detail, or unique features
- Overview shots showing the full kit contents
- Moments of discovery or reaction

Lower value:
- Extended silence with no movement (dead air)
- Repeated shots of the same angle with no new information
- Camera adjusting / out of focus / shaky footage

Sample descriptions:
{captions_text}

Metrics: interest={avg_interest:.2f}, duplication={dup_ratio:.0%}

Rate 1-10 based on showcase value and viewer engagement. Format: "Rating: X/10 - reason"."""
        else:
            prompt = f"""Rate this {scene['duration']:.0f}s video scene from scale model building on interest level (1-10).

IMPORTANT: For model building content, highly value:
- Close-up detail shots showing parts, interiors, or intricate work
- Hands-on assembly, painting, or construction work
- Clear focus on specific components (steering wheels, dashboards, etc.)
- "Lack of environmental context" is FINE for detail showcases

Sample descriptions:
{captions_text}

Metrics: interest={avg_interest:.2f}, duplication={dup_ratio:.0%}

Rate 1-10 based on craftsmanship value and detail shown. Format: "Rating: X/10 - reason"."""
        
        try:
            with suppress_cpp_output():
                output = model.create_chat_completion(
                    messages=[
                        {"role": "system", "content": "You are an expert video analyst."},
                        {"role": "user", "content": prompt}
                    ],
                    max_tokens=100,
                    temperature=0.3,
                )
            
            response = output['choices'][0]['message']['content'].strip()
            
            # Parse rating
            import re
            rating_match = re.search(r'(\d+)/10', response)
            if rating_match:
                rating = int(rating_match.group(1))
            else:
                rating = 5  # Default
            
            # Classify based on rating
            if is_unboxing:
                # Unboxing mode: always 1.0x speed, just classify for cut/keep
                if rating >= 7:
                    scene['classification'] = 'interesting'
                elif rating >= 5:
                    scene['classification'] = 'moderate'
                elif rating >= 3:
                    scene['classification'] = 'low'
                else:
                    scene['classification'] = 'boring'
                scene['speed'] = 1.0
            else:
                # Build mode: classification determines speed
                if rating >= 8:
                    scene['classification'] = 'interesting'
                    scene['speed'] = 1.0
                elif rating >= 6:
                    scene['classification'] = 'moderate'
                    scene['speed'] = 2.0
                elif rating >= 4:
                    scene['classification'] = 'low'
                    scene['speed'] = 4.0
                else:
                    scene['classification'] = 'boring'
                    scene['speed'] = 6.0
            
            scene['llm_rating'] = rating
            scene['llm_reasoning'] = response
            
            print(f"   Scene {scene['scene_num']:02d}: {scene['classification']} ({scene['speed']:.1f}x) - {response[:60]}...")
            
        except Exception as e:
            print(f"   ⚠️  LLM classification failed for scene {scene['scene_num']}: {e}")
            scene['classification'] = 'low'
            scene['speed'] = 1.0 if is_unboxing else 4.0
            scene['llm_reasoning'] = f'Error: {str(e)}'
    
    return scenes


def fallback_classify_scenes(scenes, mode='build'):
    """Fallback rule-based scene classification."""
    is_unboxing = (mode == 'unboxing')
    print(f"   Using rule-based classification (fallback, mode={mode})")
    
    for scene in scenes:
        interest = scene.get('semantic_interest', 0)
        dup_ratio = scene.get('duplicate_ratio', 0)
        boring = scene.get('semantic_boring', 0)
        
        if (boring > 0.50 and interest < 0.22) or dup_ratio > 0.80:
            scene['classification'] = 'boring'
            scene['speed'] = 1.0 if is_unboxing else 6.0
        elif dup_ratio > 0.60 or boring > 0.35:
            scene['classification'] = 'low'
            scene['speed'] = 1.0 if is_unboxing else 4.0
        elif interest > 0.34 and dup_ratio < 0.40:
            scene['classification'] = 'interesting'
            scene['speed'] = 1.0
        elif interest > 0.26:
            scene['classification'] = 'moderate'
            scene['speed'] = 1.0 if is_unboxing else 2.0
        else:
            scene['classification'] = 'low'
            scene['speed'] = 1.0 if is_unboxing else 4.0
        
        print(f"   Scene {scene['scene_num']:02d}: {scene['classification']} ({scene['speed']:.1f}x)")
    
    return scenes


def llm_select_showcases(frames, llava_model, num_showcases=3):
    """Use LLM to select diverse showcase moments."""
    print(f"\n🤖 [PASS 2 - 3/3] LLM selecting {num_showcases} showcase moments...")
    
    if llava_model is None or len(frames) < 10:
        print("   ⚠️  Skipping showcase selection")
        return []
    
    model = llava_model["model"]
    
    # Sample frames across video
    sample_count = min(20, len(frames))
    indices = np.linspace(0, len(frames)-1, sample_count, dtype=int)
    sampled_frames = [frames[i] for i in indices]
    
    # Build context
    context_parts = []
    for frame in sampled_frames:
        ts = frame['timestamp']
        caption = frame.get('caption', 'no caption')
        interest = frame.get('semantic_interest', 0)
        context_parts.append(f"{ts:.0f}s (interest={interest:.2f}): {caption}")
    
    context_text = "\n".join(context_parts)
    
    # Ask LLM to pick best moments
    prompt = f"""Select the {num_showcases} most interesting and DIVERSE moments from this scale model building video to showcase. Pick moments that:
1. Show actual work being done
2. Are visually different from each other
3. Demonstrate different aspects of the project

Timeline:
{context_text}

List {num_showcases} timestamps with brief reasons. Format: "XXs: reason"."""
    
    try:
        with suppress_cpp_output():
            output = model.create_chat_completion(
                messages=[
                    {"role": "system", "content": "You are an expert video curator."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=150,
                temperature=0.4,
            )
        
        response = output['choices'][0]['message']['content'].strip()
        print(f"   LLM showcase selection:\n{response}")
        
        # Parse timestamps
        showcases = []
        import re
        for match in re.finditer(r'(\d+)s:', response):
            ts = int(match.group(1))
            showcases.append({
                'timestamp': ts,
                'llm_reasoning': response
            })
        
        print(f"   ✓ LLM selected {len(showcases)} showcases")
        return showcases[:num_showcases]
        
    except Exception as e:
        print(f"   ⚠️  LLM showcase selection failed: {e}")
        return []


def create_scenes_from_boundaries(frames, transitions, max_scene_length=None):
    """Create scene objects from boundary timestamps.
    
    Args:
        max_scene_length: Maximum scene duration in seconds. Longer scenes will be split.
    """
    scenes = []
    
    for i in range(len(transitions) - 1):
        start_time = transitions[i]
        end_time = transitions[i+1]
        duration = end_time - start_time
        
        # Split long scenes if max_scene_length is specified
        if max_scene_length and duration > max_scene_length:
            # Create sub-scenes of max_scene_length
            num_splits = int(np.ceil(duration / max_scene_length))
            split_duration = duration / num_splits
            
            for split_idx in range(num_splits):
                split_start = start_time + (split_idx * split_duration)
                split_end = start_time + ((split_idx + 1) * split_duration)
                
                # Find frames in this sub-scene range
                scene_frames = [f for f in frames if split_start <= f['timestamp'] < split_end]
                
                if len(scene_frames) < 3:
                    continue
                
                # Calculate scene metrics
                avg_interest = np.mean([f['semantic_interest'] for f in scene_frames])
                avg_boring = np.mean([f['semantic_boring'] for f in scene_frames])
                duplicate_ratio = sum(1 for f in scene_frames if f['is_duplicate']) / len(scene_frames)
                repetitive_ratio = sum(1 for f in scene_frames if f['is_repetitive']) / len(scene_frames)
                
                scenes.append({
                    'scene_num': len(scenes) + 1,
                    'start_time': split_start,
                    'end_time': split_end,
                    'duration': split_end - split_start,
                    'frame_count': len(scene_frames),
                    'semantic_interest': float(avg_interest),
                    'semantic_boring': float(avg_boring),
                    'duplicate_ratio': float(duplicate_ratio),
                    'repetitive_ratio': float(repetitive_ratio),
                })
        else:
            # Normal scene (not too long)
            scene_frames = [f for f in frames if start_time <= f['timestamp'] < end_time]
            
            if len(scene_frames) < 5:
                continue
            
            # Calculate scene metrics
            avg_interest = np.mean([f['semantic_interest'] for f in scene_frames])
            avg_boring = np.mean([f['semantic_boring'] for f in scene_frames])
            duplicate_ratio = sum(1 for f in scene_frames if f['is_duplicate']) / len(scene_frames)
            repetitive_ratio = sum(1 for f in scene_frames if f['is_repetitive']) / len(scene_frames)
            
            scenes.append({
                'scene_num': len(scenes) + 1,
                'start_time': start_time,
                'end_time': end_time,
                'duration': duration,
                'frame_count': len(scene_frames),
                'semantic_interest': float(avg_interest),
                'semantic_boring': float(avg_boring),
                'duplicate_ratio': float(duplicate_ratio),
                'repetitive_ratio': float(repetitive_ratio),
            })
    
    print(f"   ✓ Created {len(scenes)} scenes from boundaries{' (with max length splitting)' if max_scene_length else ''}")
    return scenes


def save_results(scenes, frames, output_file, video_name):
    """Save final analysis results"""
    counts = {'interesting': 0, 'moderate': 0, 'low': 0, 'boring': 0, 'skip': 0}
    total_original = sum(s['duration'] for s in scenes)
    total_output = sum(s['duration'] / s['speed'] for s in scenes if s['speed'] > 0)
    
    for scene in scenes:
        counts[scene['classification']] += 1
    
    result = {
        'video': video_name,
        'method': 'advanced_ai_v5_two_pass_llm',
        'models': ['CLIP_ViT-B/32', 'ResNet50', 'Qwen2.5-VL-7B', 'LLM_Scene_Analysis'],
        'total_frames': len(frames),
        'total_scenes': len(scenes),
        'scenes': scenes,
        'summary': {
            'interesting': counts['interesting'],
            'moderate': counts['moderate'],
            'low': counts['low'],
            'boring': counts['boring'],
            'skip': counts['skip'],
            'original_duration': total_original,
            'output_duration': total_output,
            'compression_ratio': (1 - total_output / total_original) * 100 if total_original > 0 else 0
        }
    }
    
    with open(output_file, 'w') as f:
        json.dump(result, f, indent=2)
    
    print(f"\n💾 Saved to: {output_file}")
    return result


def print_summary(result):
    """Print analysis summary"""
    s = result['summary']
    print("\n" + "=" * 70)
    print("📊 Advanced AI Analysis v5 - Two-Pass LLM Report")
    print("=" * 70)
    print(f"  Models: {', '.join(result['models'])}")
    print(f"  Interesting (1.0x):  {s['interesting']} scenes")
    print(f"  Moderate (2.0x):     {s['moderate']} scenes")
    print(f"  Low (4.0x):          {s['low']} scenes")
    print(f"  Boring (6.0x):       {s['boring']} scenes")
    print(f"  Skip:                {s['skip']} scenes")
    print()
    print(f"  Original:    {s['original_duration']/60:.1f} min")
    print(f"  Compressed:  {s['output_duration']/60:.1f} min")
    print(f"  Saved:       {(s['original_duration']-s['output_duration'])/60:.1f} min ({s['compression_ratio']:.0f}%)")
    print("=" * 70)


def process_video_two_pass(video_path, output_dir, sample_interval, llava_model, skip_duplicate_captions=False, max_scene_length=None, mode='build', unboxing_config=None):
    """Two-pass workflow: metadata collection + LLM analysis
    
    Args:
        llava_model: Pre-loaded Qwen2.5-VL model (passed from main, reused across videos)
        skip_duplicate_captions: Skip captioning duplicate frames (saves ~10-15% time)
        max_scene_length: Maximum scene duration in seconds (e.g., 40). Splits longer scenes.
        mode: 'build' (default), 'unboxing', or 'reels'
        unboxing_config: dict with unboxing-specific settings (from config['unboxing'])
    """
    print("\n" + "=" * 70)
    print(f"🎬 Processing: {video_path.name}")
    print("=" * 70)
    
    # ========== PASS 1: METADATA COLLECTION ==========
    print("\n" + "🔵 " * 35)
    print("PASS 1: Enhanced Metadata Collection")
    print("🔵 " * 35)
    
    # Extract frames
    frames = extract_frames_parallel(video_path, sample_interval)
    
    # Load vision models
    clip_model = load_clip_model()
    resnet_model = load_resnet_model()
    
    # Vision analysis
    frames = analyze_with_clip(frames, clip_model)
    frames, features = extract_features(frames, resnet_model)
    frames = detect_duplicates_and_repetition(frames, features)
    
    # Release vision models, load Qwen2.5-VL
    print("\n🧹 Releasing vision models...")
    release_model_dict(clip_model)
    release_model_dict(resnet_model)
    clear_cuda_cache()
    
    # Model is already loaded and passed in - no need to reload!
    # Just verify it's available
    if llava_model is None:
        print("⚠️ Warning: No LLM model provided, loading fresh...")
        gc.collect()
        time.sleep(0.5)
        llava_model = load_llava_model()
    
    # Caption ALL frames early (with optional duplicate skipping)
    frames = caption_all_frames_early(frames, llava_model, max_length=20, skip_duplicates=skip_duplicate_captions)
    
    # Extract caption features
    frames = extract_caption_features(frames)
    
    # Save metadata in the same directory as the video
    metadata_path = video_path.parent / f"metadata_{video_path.stem}.json"
    save_metadata_json(frames, metadata_path, video_path.name)
    
    # ========== PASS 2: LLM SCENE ANALYSIS ==========
    print("\n" + "🟢 " * 35)
    print("PASS 2: LLM Scene Analysis")
    print("🟢 " * 35)
    
    # LLM detect scene boundaries
    transitions = llm_detect_scene_boundaries(frames, llava_model)
    
    # Create scenes from boundaries (with optional max length splitting)
    scenes = create_scenes_from_boundaries(frames, transitions, max_scene_length=max_scene_length)
    
    # Group frames by scene
    frames_by_scene = {}
    for frame in frames:
        for scene_idx, scene in enumerate(scenes):
            if scene['start_time'] <= frame['timestamp'] < scene['end_time']:
                if scene_idx not in frames_by_scene:
                    frames_by_scene[scene_idx] = []
                frames_by_scene[scene_idx].append(frame)
                break
    
    # LLM classify scenes
    scenes = llm_classify_scenes(scenes, frames_by_scene, llava_model, mode=mode)
    
    # For unboxing mode: run audio+motion analysis and refine boring detection
    if mode == 'unboxing':
        from analyze_audio import analyze_audio_and_motion, mark_scenes_from_audio_analysis
        print("\n" + "📦 " * 35)
        print("UNBOXING MODE: Audio & Motion Analysis")
        print("📦 " * 35)
        audio_result = analyze_audio_and_motion(video_path, unboxing_config or {})
        scenes = mark_scenes_from_audio_analysis(scenes, audio_result)
        # Store audio analysis in result for reference
        _audio_analysis_cache = audio_result
    else:
        _audio_analysis_cache = None
    
    # LLM select showcases
    showcases = llm_select_showcases(frames, llava_model, num_showcases=3)
    
    # Save final results in the same directory as the video
    output_file = video_path.parent / f"scene_analysis_{video_path.stem}.json"
    result = save_results(scenes, frames, output_file, video_path.name)
    
    # Add showcases to result
    result['showcases'] = showcases
    result['mode'] = mode
    if _audio_analysis_cache is not None:
        result['audio_analysis'] = {
            'silence_ranges': _audio_analysis_cache['silence_ranges'],
            'static_ranges': _audio_analysis_cache['static_ranges'],
            'boring_segments': _audio_analysis_cache['boring_segments'],
        }
    with open(output_file, 'w') as f:
        json.dump(result, f, indent=2)
    
    print_summary(result)
    
    # Don't release the model - it will be reused for next video!
    # Just clear GPU cache to free up temp memory
    clear_cuda_cache()
    gc.collect()
    
    return output_file


def main():
    """Main two-pass workflow"""
    parser = argparse.ArgumentParser(description="Two-pass LLM-enhanced video analysis")
    parser.add_argument("--config", default="project_config.json", help="Project config JSON file")
    parser.add_argument("--input-dir", default=None, help="Folder with input videos")
    parser.add_argument("--video", default=None, help="Single video file to analyze")
    parser.add_argument("--output-dir", default=None, help="Folder for analysis JSON outputs")
    parser.add_argument("--sample-interval", type=int, default=None, help="Frame sample interval (seconds)")
    parser.add_argument("--skip-duplicate-captions", action="store_true", help="Skip captioning duplicate frames (10-15%% speed boost)")
    parser.add_argument("--max-scene-length", type=int, default=None, help="Max scene duration in seconds (e.g., 40). Splits longer scenes.")
    parser.add_argument("--mode", default=None, choices=['build', 'unboxing', 'reels'], help="Pipeline mode: build (default), unboxing (keep audio/speed), reels")
    args = parser.parse_args()

    config = load_project_config(args.config)
    paths_cfg = config.get("paths", {})
    analysis_cfg = config.get("analysis", {})
    mode = args.mode or config.get('mode', 'build')
    unboxing_config = config.get('unboxing', {}) if mode == 'unboxing' else None

    sample_interval = args.sample_interval if args.sample_interval is not None else analysis_cfg.get("sample_interval", SAMPLE_INTERVAL)

    global TARGET_OUTPUT_RATIO
    global MAX_SPEED_MULTIPLIER
    if "target_output_ratio" in analysis_cfg:
        TARGET_OUTPUT_RATIO = float(analysis_cfg["target_output_ratio"])
    if "max_speed_multiplier" in analysis_cfg:
        MAX_SPEED_MULTIPLIER = float(analysis_cfg["max_speed_multiplier"])
    
    output_dir = Path(args.output_dir or paths_cfg.get("output_dir") or ".")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    video_arg = args.video or paths_cfg.get("video")
    if video_arg:
        video_paths = [Path(video_arg)]
    else:
        input_dir = Path(args.input_dir or paths_cfg.get("video_dir") or paths_cfg.get("input_dir") or ".")
        video_paths = [
            p for p in input_dir.iterdir()
            if p.is_file() and p.suffix.lower() in {".mov", ".mp4", ".mkv"}
        ]
        video_paths.sort(key=lambda p: p.name.lower())
    
    if not video_paths:
        print("❌ No videos found to analyze.")
        return
    
    output_files = []
    skipped_videos = []
    
    # Count videos that need processing
    videos_to_process = []
    for video_path in video_paths:
        if not video_path.exists():
            print(f"❌ Video not found: {video_path}")
            continue
        
        metadata_file = video_path.parent / f"metadata_{video_path.stem}.json"
        scene_analysis_file = video_path.parent / f"scene_analysis_{video_path.stem}.json"
        
        if metadata_file.exists() and scene_analysis_file.exists():
            print(f"\n⏭️  Skipping {video_path.name} - analysis already complete")
            print(f"   (Found: {metadata_file.name} + {scene_analysis_file.name})")
            # For unboxing mode: ensure audio analysis is applied
            # (may be missing if video was analyzed before unboxing mode existed,
            #  or if audio trimming logic was updated)
            if mode == 'unboxing':
                from analyze_audio import analyze_audio_and_motion, mark_scenes_from_audio_analysis
                with open(scene_analysis_file) as f:
                    cached = json.load(f)
                cached_scenes = cached.get('scenes', [])
                # Check if audio analysis is complete and up-to-date
                has_silence_data = len(cached.get('audio_analysis', {}).get('silence_ranges', [])) > 0
                has_trimming = any(s.get('audio_trimmed') for s in cached_scenes)
                needs_audio = not has_silence_data or not has_trimming
                if needs_audio:
                    print(f"   🔄 Running audio analysis (voice detection + scene trimming)...")
                    audio_result = analyze_audio_and_motion(video_path, unboxing_config or {})
                    cached_scenes = mark_scenes_from_audio_analysis(cached_scenes, audio_result)
                    cached['scenes'] = cached_scenes
                    cached['audio_analysis'] = {
                        'silence_ranges': audio_result['silence_ranges'],
                        'static_ranges': audio_result['static_ranges'],
                        'boring_segments': audio_result['boring_segments'],
                    }
                    with open(scene_analysis_file, 'w') as f:
                        json.dump(cached, f, indent=2)
                    print(f"   ✅ Audio analysis applied to {len(cached_scenes)} scenes")
            skipped_videos.append(video_path)
            output_files.append(scene_analysis_file)
        else:
            videos_to_process.append(video_path)
    
    # Load LLM model once for all videos (avoid repeated disk reads)
    llava_model = None
    if videos_to_process:
        print("\n" + "=" * 70)
        print(f"🤖 Loading Qwen2.5-VL-7B model (will be reused for {len(videos_to_process)} video(s))")
        print("=" * 70)
        llava_model = load_llava_model()
        if llava_model is None:
            print("❌ Failed to load LLM model. Cannot proceed with analysis.")
            return

    for video_path in videos_to_process:
        try:
            output_file = process_video_two_pass(
                video_path, 
                output_dir, 
                sample_interval,
                llava_model,  # Pass the pre-loaded model
                skip_duplicate_captions=args.skip_duplicate_captions,
                max_scene_length=args.max_scene_length,
                mode=mode,
                unboxing_config=unboxing_config,
            )
            output_files.append(output_file)
        except Exception as e:
            print(f"\n❌ Failed to process {video_path.name}: {e}")
            import traceback
            traceback.print_exc()
            print("\n🔄 Continuing with next video...")
            continue
        finally:
            # Just clear GPU cache between videos, keep model loaded
            clear_cuda_cache()
            gc.collect()
    
    # Release the model only after ALL videos are processed
    if llava_model:
        print("\n" + "=" * 70)
        print("🧹 Releasing Qwen2.5-VL model (all videos complete)")
        print("=" * 70)
        release_model_dict(llava_model)
        llava_model = None
        clear_cuda_cache()
        gc.collect()
    
    if output_files:
        print("\n" + "=" * 70)
        print("📊 Batch Analysis Complete")
        print("=" * 70)
        print(f"  Processed: {len(output_files) - len(skipped_videos)} videos")
        print(f"  Skipped: {len(skipped_videos)} videos (already analyzed)")
        print(f"  Total: {len(output_files)} videos ready")
        print("=" * 70)
        print(f"\n💡 Next: python extract_scenes.py --analysis-dir {output_dir}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Test Qwen2-VL-7B with GPU acceleration for video analysis.
This script loads a test frame and verifies GPU usage.
"""
import cv2
import base64
from pathlib import Path
from llama_cpp import Llama
import time
import subprocess

print("=" * 70)
print("🎬 Video Frame Analysis Test with GPU")
print("=" * 70)

# Check GPU with nvidia-smi
try:
    result = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                          capture_output=True, text=True)
    gpu_info = result.stdout.strip()
    print(f"\n💎 GPU: {gpu_info}")
except:
    print("\n⚠️  No GPU detected")

# Load model
print("\n🤖 Loading Qwen2.5-VL-7B GGUF model...")
model_path = Path.home() / ".cache/huggingface/hub/models--unsloth--Qwen2.5-VL-7B-Instruct-GGUF/snapshots/68bb8bc4b7df5289c143aaec0ab477a7d4051aab/Qwen2.5-VL-7B-Instruct-Q4_K_M.gguf"

start = time.time()
llm = Llama(
    model_path=str(model_path),
    n_gpu_layers=-1,  # ALL layers on GPU
    n_ctx=512,
    verbose=True  # Show layer assignments
)
load_time = time.time() - start
print(f"\n✓ Model loaded in {load_time:.1f}s")

# Extract a test frame
print("\n📹 Extracting test frame from first available MOV...")
import glob
video_files = sorted(glob.glob("/home/mazsola/ai-video-editor/giulia-part-5/*.MOV"))
if not video_files:
    print("❌ No MOV files found in giulia-part-5/")
    exit(1)
video_path = Path(video_files[0])
if not video_path.exists():
    print(f"❌ Video not found: {video_path}")
    exit(1)

cap = cv2.VideoCapture(str(video_path))
cap.set(cv2.CAP_PROP_POS_MSEC, 5000)  # 5 seconds in
ret, frame = cap.read()
cap.release()

if not ret:
    print("❌ Failed to extract frame")
    exit(1)

# Save frame
test_frame_path = Path("/tmp/test_frame.jpg")
cv2.imwrite(str(test_frame_path), frame)
print(f"✓ Saved test frame: {test_frame_path}")

# Encode image to base64
with open(test_frame_path, "rb") as f:
    image_data = base64.b64encode(f.read()).decode("utf-8")

# Test inference
print("\n🧠 Running inference (check nvidia-smi in another terminal)...")
print("   Prompt: 'Describe what you see in this image.'")

start = time.time()
response = llm.create_chat_completion(
    messages=[
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe what you see in this image in one sentence."},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}}
            ]
        }
    ],
    max_tokens=100,
    temperature=0.7,
)
inference_time = time.time() - start

print(f"\n✓ Inference completed in {inference_time:.1f}s")
print(f"\n📝 Response:")
print(response["choices"][0]["message"]["content"])

print("\n" + "=" * 70)
print("✓ Test complete! Model is using GPU for inference.")
print("=" * 70)

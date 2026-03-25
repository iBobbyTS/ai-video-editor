#!/bin/bash
set -e

echo "============================================================"
echo "Building llama-cpp-python with GCC 12 + CUDA"
echo "============================================================"

GCC12="/opt/gcc-12/bin/gcc"
GXX12="/opt/gcc-12/bin/g++"

# Check GCC 12 exists
if [ ! -f "$GCC12" ]; then
    echo "❌ GCC 12 not found at $GCC12"
    echo "Run ./install_gcc12.sh first"
    exit 1
fi

echo ""
echo "Using GCC 12:"
"$GCC12" --version | head -1

cd /home/mazsola/ai-video-editor
source .venv/bin/activate

echo ""
echo "Uninstalling old llama-cpp-python..."
pip uninstall -y llama-cpp-python || true

echo ""
echo "Building llama-cpp-python with CUDA + GCC 12..."
echo "This will take 5-10 minutes..."

# Force nvcc to use GCC 12 with its own headers/libraries
export CUDAHOSTCXX="$GXX12"
export PATH="/opt/gcc-12/bin:$PATH"
export LD_LIBRARY_PATH="/opt/gcc-12/lib64:$LD_LIBRARY_PATH"
export CPLUS_INCLUDE_PATH="/opt/gcc-12/include/c++/12.3.0:/opt/gcc-12/include/c++/12.3.0/x86_64-pc-linux-gnu"
export CXXFLAGS="-I/opt/gcc-12/include/c++/12.3.0 -I/opt/gcc-12/include/c++/12.3.0/x86_64-pc-linux-gnu"

CMAKE_ARGS="-DGGML_CUDA=on -DCMAKE_CUDA_COMPILER=/usr/local/cuda/bin/nvcc -DCMAKE_CUDA_HOST_COMPILER=$GXX12 -DCMAKE_C_COMPILER=$GCC12 -DCMAKE_CXX_COMPILER=$GXX12 -DCMAKE_CUDA_FLAGS=-allow-unsupported-compiler" \
    pip install llama-cpp-python --no-cache-dir --verbose 2>&1 | tee /tmp/llama_gcc12_build.log

echo ""
echo "============================================================"
echo "✅ Build complete!"
echo "============================================================"
echo ""
echo "Testing GPU support..."
python tools/test_video_gpu.py

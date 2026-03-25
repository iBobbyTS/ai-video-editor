#!/bin/bash
set -e

echo "============================================================"
echo "Building GCC 12 from source for CUDA compatibility"
echo "This will take 30-60 minutes depending on your CPU"
echo "============================================================"

# Install build dependencies (matching README_GPU_COMPILATION.md prerequisites)
echo ""
echo "Installing build dependencies..."
sudo dnf install -y wget tar bzip2 gcc gcc-c++ make \
    libmpc-devel mpfr-devel gmp-devel isl-devel \
    zlib-devel flex bison texinfo \
    ninja-build cmake git python3-devel

# Create build directory
BUILD_DIR="/tmp/gcc-12-build"
INSTALL_DIR="/opt/gcc-12"

mkdir -p "$BUILD_DIR"
cd "$BUILD_DIR"

# Download GCC 12.3.0 (last stable GCC 12)
echo ""
echo "Downloading GCC 12.3.0..."
if [ ! -f gcc-12.3.0.tar.gz ]; then
    wget https://ftp.gnu.org/gnu/gcc/gcc-12.3.0/gcc-12.3.0.tar.gz
fi

echo "Extracting..."
tar xzf gcc-12.3.0.tar.gz
cd gcc-12.3.0

# Configure for minimal build (C/C++ only, faster)
echo ""
echo "Configuring GCC (C/C++ only for faster build)..."
# Clean any previous build to avoid corrupted artifacts from failed runs
rm -rf build
mkdir build
cd build

../configure \
    --prefix="$INSTALL_DIR" \
    --enable-languages=c,c++ \
    --disable-multilib \
    --disable-bootstrap \
    --disable-libsanitizer

# Build using all CPU cores
echo ""
echo "Building GCC 12.3.0 (this takes 30-60 minutes)..."
echo "Using $(nproc) CPU cores"
make -j$(nproc)

# Install
echo ""
echo "Installing to $INSTALL_DIR..."
sudo make install

# Verify
echo ""
echo "============================================================"
echo "✅ GCC 12 installed successfully!"
echo "============================================================"
echo ""
echo "Location: $INSTALL_DIR"
echo "GCC version:"
"$INSTALL_DIR/bin/gcc" --version | head -1
"$INSTALL_DIR/bin/g++" --version | head -1
echo ""
echo "Now run: ./build_llama_cpp_with_gcc12.sh"

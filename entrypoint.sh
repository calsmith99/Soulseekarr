#!/bin/sh
# Entrypoint script for Music Management Tools Web Interface

set -e

echo "🚀 Starting Music Management Tools Web Interface..."
echo "📦 Container image: $(cat /etc/alpine-release 2>/dev/null || echo 'Unknown')"
echo "🐍 Python version: $(python3 --version)"

# Install system dependencies
echo "📥 Installing system dependencies..."
apk add --no-cache curl jq bash ffmpeg

# Install additional dependencies for psutil (system monitoring)
echo "🔧 Installing build dependencies for system monitoring..."
apk add --no-cache --virtual .psutil-build-deps \
    gcc \
    musl-dev \
    linux-headers \
    python3-dev

# Install core Python dependencies first
echo "📥 Installing core Python dependencies..."
if [ -f /data/requirements-core.txt ]; then
    pip install --no-cache-dir -r /data/requirements-core.txt
elif [ -f /data/requirements.txt ]; then
    pip install --no-cache-dir -r /data/requirements.txt
else
    echo "⚠️  No requirements file found, installing minimal dependencies..."
    pip install --no-cache-dir flask>=2.3.0 Werkzeug>=2.3.0 requests>=2.25.0
fi

# Try to install MusicBrainz dependencies (optional)
echo "🎵 Attempting to install MusicBrainz dependencies (optional)..."
MUSICBRAINZ_DISABLED=0

# Install system dependencies for MusicBrainz
if ! apk add --no-cache --virtual .build-deps \
    python3-dev \
    gcc \
    musl-dev \
    fftw-dev \
    chromaprint-dev 2>/dev/null; then
    echo "⚠️  Warning: Failed to install MusicBrainz build dependencies"
    echo "   MusicBrainz functionality will be disabled"
    MUSICBRAINZ_DISABLED=1
fi

# Install runtime dependencies
if [ "$MUSICBRAINZ_DISABLED" = "0" ]; then
    if ! apk add --no-cache \
        chromaprint \
        fftw 2>/dev/null; then
        echo "⚠️  Warning: Failed to install MusicBrainz runtime dependencies"
        echo "   MusicBrainz functionality will be disabled"
        MUSICBRAINZ_DISABLED=1
    fi
fi

# Install MusicBrainz Python dependencies if build was successful
if [ "$MUSICBRAINZ_DISABLED" = "0" ] && [ -f /data/requirements-musicbrainz.txt ]; then
    echo "🎵 Installing MusicBrainz Python packages..."
    if ! pip install --no-cache-dir -r /data/requirements-musicbrainz.txt 2>/dev/null; then
        echo "⚠️  Warning: MusicBrainz Python packages failed to install"
        echo "   MusicBrainz functionality will be disabled"
        MUSICBRAINZ_DISABLED=1
    fi
fi

# Clean up build dependencies to save space
if [ "$MUSICBRAINZ_DISABLED" = "0" ]; then
    echo "🧹 Cleaning up MusicBrainz build dependencies..."
    apk del .build-deps 2>/dev/null || true
fi

# Clean up psutil build dependencies
echo "🧹 Cleaning up psutil build dependencies..."
apk del .psutil-build-deps 2>/dev/null || true

# Create logs directory
mkdir -p /logs

# Set permissions for scripts
echo "🔐 Setting script permissions..."
chmod +x /data/*.sh 2>/dev/null || true
chmod +x /data/app.py 2>/dev/null || true

# Display configuration
echo ""
echo "⚙️  Configuration:"
echo "   📍 Base URL: ${BASE_URL}"
echo "   👤 User: ${USER}"
echo "   📁 Music Directory: ${MUSIC_DIR}"
echo "   📅 Cleanup Days: ${CLEANUP_DAYS}"
echo "   🧪 Dry Run: ${DRY_RUN}"
echo "   🎵 MusicBrainz: $(if [ "$MUSICBRAINZ_DISABLED" = "0" ]; then echo "✅ Available"; else echo "❌ Disabled"; fi)"
echo "   🔑 AcoustID API Key: $(if [ -n "${ACOUSTID_API_KEY}" ]; then echo "✅ Set"; else echo "❌ Not set (audio fingerprinting disabled)"; fi)"
echo "   🌐 Web Interface: http://0.0.0.0:${PORT:-5000}"
echo ""

# Start the web interface
echo "🌐 Starting web interface on port ${PORT:-5000}..."
exec python3 /data/app.py
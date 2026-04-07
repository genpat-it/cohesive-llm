# 1. Use Python 3.12 Slim (Lightweight & Fast)
FROM python:3.12-slim

# 2. Set Environment Variables
# Ensures Python output is sent straight to terminal (ensures persistent logging)
ENV PYTHONUNBUFFERED=1
# Set HuggingFace cache to /tmp, because some restricted filesystems are read-only
# except for specific folders, and /tmp is always writable.
ENV HF_HOME=/tmp/huggingface

# 3. Set Working Directory
WORKDIR /app

# 4. Install System Dependencies
# 'git' is often needed for installing python packages directly from repos
# 'build-essential' helps with compiling C-extensions (like numpy/faiss)
RUN apt-get update && apt-get install -y \
    build-essential \
    git \
    && rm -rf /var/lib/apt/lists/*

# --- THE CACHE TRICK ---
# Copy ONLY requirements first. Docker will cache this layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. Copy the rest of the application
COPY . .

# 6. Permissions Fix for Restricted Environments
# Some platforms run containers as a random user ID. We need to make sure
# that user can read/execute our files.
RUN chgrp -R 0 /app && \
    chmod -R g=u /app

# 7. Start the Server
# We use the PORT environment variable which orchestrators often set automatically
CMD ["sh", "-c", "uvicorn app.api:app --host 0.0.0.0 --port 8080"]
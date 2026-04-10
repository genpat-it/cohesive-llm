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
    default-jre-headless \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Nextflow and pre-download its dependencies
RUN curl -fsSL https://get.nextflow.io | bash && \
    mv nextflow /usr/local/bin/ && \
    chmod +x /usr/local/bin/nextflow && \
    NXF_HOME=/usr/local/share/nextflow nextflow -version && \
    chmod -R a+r /usr/local/share/nextflow

ENV NXF_HOME=/usr/local/share/nextflow
ENV NXF_OFFLINE=true

# --- THE CACHE TRICK ---
# Copy ONLY requirements first. Docker will cache this layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. Copy the rest of the application
COPY . .

# 6. Permissions Fix for Restricted Environments
# Some platforms (e.g. OpenShift) run containers as a random user ID belonging
# to the root group. We make /app readable/executable by the root group so it
# works both with our explicit non-root user and with random-UID schedulers.
RUN chgrp -R 0 /app && \
    chmod -R g=u /app

# 7. Create a dedicated non-root user and switch to it.
# Hugging Face cache lives under /tmp (HF_HOME above) which is world-writable,
# so the non-root user can still download models on first start.
RUN groupadd -r app && useradd -r -g app -u 1000 -m -d /home/app app && \
    chown -R app:app /app
USER 1000

# 8. Start the Server
CMD ["sh", "-c", "uvicorn app.api:app --host 0.0.0.0 --port 8080"]
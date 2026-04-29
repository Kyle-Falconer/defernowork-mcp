FROM mcr.microsoft.com/playwright/python@sha256:f3a3d2e0332df4c7b6992db0ad0687df1653efd4671612b8ca1be6ae5fc06448

RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
 && apt-get install -y nodejs \
 && rm -rf /var/lib/apt/lists/*

ARG CLAUDE_CODE_VERSION=2.0.30
RUN npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY capture-url.sh /usr/local/bin/capture-url.sh
RUN chmod +x /usr/local/bin/capture-url.sh

# FIFO for the BROWSER hook. Pre-create at image-build time so it
# exists at container start. Do NOT add tmpfs:[/tmp] to the runner
# service — it would shadow this FIFO. World-writable so pwuser (the
# non-root runtime user) can write to it from the BROWSER hook.
RUN mkfifo /tmp/auth-url.fifo && chmod 0666 /tmp/auth-url.fifo

# Run as non-root. Claude Code refuses --dangerously-skip-permissions
# under root for security reasons; the bottle uses that flag inside the
# container to bypass non-interactive permission gates.
USER pwuser
ENV HOME=/home/pwuser
WORKDIR /work
CMD ["pytest", "/work", "-v"]

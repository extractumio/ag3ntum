FROM ubuntu:24.04

LABEL org.opencontainers.image.title="ag3ntum"

ENV DEBIAN_FRONTEND=noninteractive

# Fetch GitHub CLI GPG key using Docker ADD (no curl needed)
ADD https://cli.github.com/packages/githubcli-archive-keyring.gpg /usr/share/keyrings/githubcli-archive-keyring.gpg

RUN chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        python3-venv \
        python3-pip \
        python3-dev \
        build-essential \
        ca-certificates \
        git \
        git-lfs \
        gh \
        openssh-client \
        nodejs \
        npm \
        bubblewrap \
        sudo \
        libmagic1 \
    && rm -rf /var/lib/apt/lists/* \
    && git lfs install

# Create ag3ntum group and ag3ntum_api user (UID 45045, well outside typical user range)
# The ag3ntum group is used for session directory access (API + user both need access)
RUN groupadd ag3ntum \
    && useradd -m -u 45045 -s /bin/bash -G ag3ntum ag3ntum_api

# Create /users directory with proper permissions
RUN mkdir -p /users && chmod 755 /users

# Create external mount point directories
# /mounts/ro - for read-only external mounts (host folders mounted as read-only)
# /mounts/rw - for read-write external mounts (host folders mounted as read-write)
# These will be bind-mounted into sandbox at /workspace/external/ro and /workspace/external/rw
RUN mkdir -p /mounts/ro /mounts/rw \
    && chmod 755 /mounts /mounts/ro /mounts/rw

# Configure sudoers for PRODUCTION - restricted access only
# Allow both -m (create home) and -M (don't create home) for useradd
# Note: sudoers uses * as wildcard, [0-9] patterns need escaping or simpler wildcards
#
# SECURITY: Test-only sudoers rules are NOT included here.
# They are injected at runtime via docker-compose.test.yml for test runs only.
# See config/test/sudoers-test for test-specific rules.
RUN echo 'ag3ntum_api ALL=(root) NOPASSWD: /usr/sbin/useradd -m -d /users/* -s /bin/bash -u * *' > /etc/sudoers.d/ag3ntum && \
    echo 'ag3ntum_api ALL=(root) NOPASSWD: /usr/sbin/useradd -M -d /users/* -s /bin/bash -u * *' >> /etc/sudoers.d/ag3ntum && \
    echo 'ag3ntum_api ALL=(root) NOPASSWD: /usr/sbin/useradd -M -d /users/* -s /bin/bash -u * -G ag3ntum *' >> /etc/sudoers.d/ag3ntum && \
    echo 'ag3ntum_api ALL=(root) NOPASSWD: /usr/sbin/usermod -L *' >> /etc/sudoers.d/ag3ntum && \
    echo 'ag3ntum_api ALL=(root) NOPASSWD: /usr/sbin/usermod -a -G ag3ntum *' >> /etc/sudoers.d/ag3ntum && \
    echo '# Restricted userdel - only session users (user_ prefix) can be deleted' >> /etc/sudoers.d/ag3ntum && \
    echo 'ag3ntum_api ALL=(root) NOPASSWD: /usr/sbin/userdel user_*' >> /etc/sudoers.d/ag3ntum && \
    echo 'ag3ntum_api ALL=(root) NOPASSWD: /usr/sbin/userdel -r user_*' >> /etc/sudoers.d/ag3ntum && \
    echo 'ag3ntum_api ALL=(root) NOPASSWD: /usr/bin/chown -R *\:* /users/*' >> /etc/sudoers.d/ag3ntum && \
    echo 'ag3ntum_api ALL=(root) NOPASSWD: /usr/bin/chown *\:* /users/*' >> /etc/sudoers.d/ag3ntum && \
    echo 'ag3ntum_api ALL=(root) NOPASSWD: /usr/bin/chgrp ag3ntum /users/*' >> /etc/sudoers.d/ag3ntum && \
    echo 'ag3ntum_api ALL=(root) NOPASSWD: /usr/bin/chgrp -R ag3ntum /users/*' >> /etc/sudoers.d/ag3ntum && \
    echo '# chown for web container node_modules (Docker named volume ownership fix)' >> /etc/sudoers.d/ag3ntum && \
    echo 'ag3ntum_api ALL=(root) NOPASSWD: /usr/bin/chown -R *\:* /src/web_terminal_client/*' >> /etc/sudoers.d/ag3ntum && \
    echo '# bwrap is required for sandbox execution' >> /etc/sudoers.d/ag3ntum && \
    echo 'ag3ntum_api ALL=(ALL) NOPASSWD: /usr/bin/bwrap *' >> /etc/sudoers.d/ag3ntum && \
    echo 'ag3ntum_api ALL=(root) NOPASSWD: /usr/bin/chmod * /users/*' >> /etc/sudoers.d/ag3ntum && \
    chmod 440 /etc/sudoers.d/ag3ntum

ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv ${VIRTUAL_ENV}
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

WORKDIR /

# Copy all requirements files
COPY requirements-base.txt requirements-legacy-cpu.txt requirements-modern-cpu.txt /

# Detect CPU capabilities and install appropriate packages
# X86_V2 requires SSE4.2 - check if CPU supports it
# Legacy CPUs (old QEMU, pre-2010) don't have SSE4.2
RUN pip install --no-cache-dir -r /requirements-base.txt \
    && if grep -q sse4_2 /proc/cpuinfo 2>/dev/null; then \
         echo "Modern CPU detected (SSE4.2 supported) - installing latest numpy/pandas"; \
         pip install --no-cache-dir -r /requirements-modern-cpu.txt; \
       else \
         echo "Legacy CPU detected (no SSE4.2) - installing compatible numpy/pandas"; \
         pip install --no-cache-dir --only-binary :all: -r /requirements-legacy-cpu.txt; \
       fi

COPY . /

# Copy and make entrypoint executable
COPY entrypoint-web.sh /entrypoint-web.sh
RUN chmod +x /entrypoint-web.sh

# Create runtime directories and set ownership of application directories to ag3ntum_api
RUN mkdir -p /data /sessions \
    && chown -R ag3ntum_api:ag3ntum_api /src /config /prompts /skills /data /users /opt/venv /sessions /mounts \
    && chown ag3ntum_api:ag3ntum_api /entrypoint-web.sh /requirements-base.txt /requirements-legacy-cpu.txt /requirements-modern-cpu.txt

ENV AG3NTUM_ROOT=/
ENV PYTHONPATH=/
ENV PYTHONUNBUFFERED=1

# UID Security Mode Configuration
# ISOLATED (default): UIDs from 50000-60000, safer for multi-tenant
# DIRECT: UIDs map to host UIDs (1000-65533), simpler for dev
# Set via docker-compose environment or CLI: -e AG3NTUM_UID_MODE=direct
ENV AG3NTUM_UID_MODE=isolated

# Switch to non-root user
USER ag3ntum_api

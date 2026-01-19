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

# Create ag3ntum_api user (UID 45045, well outside typical user range to avoid conflicts)
RUN useradd -m -u 45045 -s /bin/bash ag3ntum_api

# Create /users directory with proper permissions
RUN mkdir -p /users && chmod 755 /users

# Create external mount point directories
# /mounts/ro - for read-only external mounts (host folders mounted as read-only)
# /mounts/rw - for read-write external mounts (host folders mounted as read-write)
# These will be bind-mounted into sandbox at /workspace/external/ro and /workspace/external/rw
RUN mkdir -p /mounts/ro /mounts/rw \
    && chmod 755 /mounts /mounts/ro /mounts/rw

# Configure sudoers for restricted access
# Allow both -m (create home) and -M (don't create home) for useradd
# Note: sudoers uses * as wildcard, [0-9] patterns need escaping or simpler wildcards
RUN echo 'ag3ntum_api ALL=(root) NOPASSWD: /usr/sbin/useradd -m -d /users/* -s /bin/bash -u * *' > /etc/sudoers.d/ag3ntum && \
    echo 'ag3ntum_api ALL=(root) NOPASSWD: /usr/sbin/useradd -M -d /users/* -s /bin/bash -u * *' >> /etc/sudoers.d/ag3ntum && \
    echo 'ag3ntum_api ALL=(root) NOPASSWD: /usr/sbin/usermod -L *' >> /etc/sudoers.d/ag3ntum && \
    echo 'ag3ntum_api ALL=(root) NOPASSWD: /usr/bin/chown -R *\:* /users/*' >> /etc/sudoers.d/ag3ntum && \
    echo 'ag3ntum_api ALL=(root) NOPASSWD: /usr/bin/chown *\:* /users/*' >> /etc/sudoers.d/ag3ntum && \
    chmod 440 /etc/sudoers.d/ag3ntum

ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv ${VIRTUAL_ENV}
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

WORKDIR /

COPY requirements.txt /requirements.txt
RUN pip install --no-cache-dir -r /requirements.txt

COPY . /

# Copy and make entrypoint executable
COPY entrypoint-web.sh /entrypoint-web.sh
RUN chmod +x /entrypoint-web.sh

# Create runtime directories and set ownership of application directories to ag3ntum_api
RUN mkdir -p /data /sessions \
    && chown -R ag3ntum_api:ag3ntum_api /src /config /prompts /skills /data /users /opt/venv /sessions /mounts \
    && chown ag3ntum_api:ag3ntum_api /entrypoint-web.sh /requirements.txt

ENV AG3NTUM_ROOT=/
ENV PYTHONPATH=/
ENV PYTHONUNBUFFERED=1

# Switch to non-root user
USER ag3ntum_api

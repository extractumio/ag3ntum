FROM ubuntu:24.04

LABEL org.opencontainers.image.title="ag3ntum"

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        python3-venv \
        python3-pip \
        python3-dev \
        build-essential \
        ca-certificates \
        curl \
        git \
        nodejs \
        npm \
        bubblewrap \
        sudo \
    && rm -rf /var/lib/apt/lists/*

# Create ag3ntum_api user (UID 45045, well outside typical user range to avoid conflicts)
RUN useradd -m -u 45045 -s /bin/bash ag3ntum_api

# Create /users directory with proper permissions
RUN mkdir -p /users && chmod 755 /users

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
    && chown -R ag3ntum_api:ag3ntum_api /src /config /prompts /skills /data /users /opt/venv /sessions \
    && chown ag3ntum_api:ag3ntum_api /entrypoint-web.sh /requirements.txt

ENV AG3NTUM_ROOT=/
ENV PYTHONPATH=/
ENV PYTHONUNBUFFERED=1

# Switch to non-root user
USER ag3ntum_api

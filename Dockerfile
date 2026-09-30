# Google Cloud Python environment for the programmable-cloud lab on Ubuntu 26.04
FROM ubuntu:26.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 \
      python3-pip \
      python3-venv \
      git \
      curl \
      openssh-client \
      ca-certificates \
      && rm -rf /var/lib/apt/lists/*

# Google Cloud client libraries used by the lab programs
RUN python3 -m pip install --break-system-packages \
      google-api-python-client \
      google-auth \
      google-auth-httplib2 \
      google-auth-oauthlib \
      && rm -rf /root/.cache/pip*

# python -> python3 symlink (some scripts use "#!/usr/bin/env python")
RUN ln -sf /usr/bin/python3 /usr/bin/python

RUN mkdir -p /lab
WORKDIR /lab

CMD ["/bin/bash"]

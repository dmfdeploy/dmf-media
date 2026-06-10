# SPDX-FileCopyrightText: 2025 Contributors to the Media eXchange Layer project.
# SPDX-License-Identifier: Apache-2.0
#
# Consolidated MXL demo image (SPIKE). One image, all demo media functions —
# entrypoints differ per chart container (the mxl base COPYs every /usr/bin/mxl-*):
#   - libfabric (build + runtime)         → the OFI fabrics backend (mxl-fabrics-demo)
#   - -DMXL_ENABLE_FABRICS_OFI=ON         → builds lib/fabrics + tools/mxl-fabrics-demo
#   - GStreamer (build + runtime)         → builds tools/mxl-gst → mxl-gst-testsrc /
#                                           mxl-gst-sink / mxl-gst-looping-filesrc
#   - fake-reader.sh                      → the mxl-fake-reader function (pure bash)
#   - iproute2 in the runtime             → entrypoint detects the eth0 IP for --node
# Built for ARM64 (the Aliyun media nodes). Build context = the MXL source root
# (dmfdeploy/mxl or an upstream checkout). See dmf-media/docs/mxl-fabrics-runbook.md.
FROM debian:trixie-slim AS base

RUN apt-get update && apt-get install -y \
      libfabric1 \
      iproute2 \
      gstreamer1.0-plugins-good \
      gstreamer1.0-plugins-base \
      python3 \
      curl zip unzip tar \
      dumb-init \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m mxl

FROM base AS builder

RUN apt-get update && apt-get install -y \
      build-essential bison flex \
      cmake ninja-build \
      pkg-config git \
      libfabric-dev \
      libgstreamer1.0-dev \
      libgstreamer-plugins-base1.0-dev \
    && rm -rf /var/lib/apt/lists/*

USER mxl
WORKDIR /home/mxl

RUN git clone https://github.com/microsoft/vcpkg.git "${HOME}/vcpkg" \
    && "${HOME}/vcpkg/bootstrap-vcpkg.sh" -disableMetrics

RUN mkdir mxl
COPY CMakeLists.txt mxl/CMakeLists.txt
COPY CMakePresets.json mxl/CMakePresets.json
COPY Doxyfile.in mxl/Doxyfile.in
COPY vcpkg.json mxl/vcpkg.json
COPY lib mxl/lib
COPY cmake mxl/cmake
COPY tools mxl/tools
COPY utils mxl/utils

# Make mxl-gst-sink's video sink env-configurable (MXL_GST_VIDEO_SINK) so the demo
# status sidecar can emit headless JPEG snapshots of the received flow. Patch is
# applied to the build context only; the upstream clone is untouched.
COPY patch-sink.py /tmp/patch-sink.py
# COPY'd sources are root-owned; patch as root then drop back to the build user.
USER 0:0
RUN python3 /tmp/patch-sink.py
USER mxl

# Fabrics opt-in + GStreamer present → tools/mxl-gst builds (mxl-gst-testsrc/sink/
# looping-filesrc, gated by gstreamer_FOUND). BUILD_UTILS=OFF skips the redundant
# utils/gst-looping-filesrc copy; tests/docs off to cut build time.
RUN cmake -S "${HOME}/mxl" --preset Linux-GCC-Release \
      -DCMAKE_INSTALL_PREFIX=/usr \
      -DMXL_ENABLE_FABRICS_OFI=ON \
      -DBUILD_UTILS=OFF \
      -DBUILD_TESTS=OFF \
      -DBUILD_DOCS=OFF
RUN ninja -C "${HOME}/mxl/build/Linux-GCC-Release"

USER 0:0
RUN ninja -C "/home/mxl/mxl/build/Linux-GCC-Release" install

FROM base AS mxl-fabrics-demo
# aarch64 triplet (ARM64 media nodes). For x86 builds swap to x86_64-linux-gnu.
COPY --from=builder /usr/lib/aarch64-linux-gnu/libmxl* /usr/lib/aarch64-linux-gnu/
COPY --from=builder /usr/bin/mxl-* /usr/bin/
# mxl-fake-reader function (pure-bash flow reader from the upstream examples).
COPY examples/scripts/fake-reader.sh /usr/local/bin/fake-reader.sh
RUN chmod +x /usr/local/bin/fake-reader.sh
# MXL demo status sidecar (JSON flow stats + JPEG preview) for the dmf-cms page.
COPY mxl-status-server.py /usr/local/bin/mxl-status-server.py
RUN chmod +x /usr/local/bin/mxl-status-server.py
USER mxl
WORKDIR /home/mxl
ENTRYPOINT ["/usr/bin/dumb-init", "--"]

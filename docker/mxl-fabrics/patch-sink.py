#!/usr/bin/env python3
"""Build-time patch: make mxl-gst-sink's video sink env-configurable.

Upstream `tools/mxl-gst/sink.cpp` hardcodes `autovideosink` (display only). The
status sidecar needs a headless JPEG sink, so we let `MXL_GST_VIDEO_SINK` override
the sink tail (default unchanged → autovideosink). Applied to the build context so
the upstream clone stays pristine. Run from the MXL source root.
"""
import sys

PATH = "mxl/tools/mxl-gst/sink.cpp"

OLD = '''                "videoscale ! "
                "autovideosink ts-offset={}",
                _config.frameWidth,
                _config.frameHeight,
                _config.frameRate.numerator,
                _config.frameRate.denominator,
                _config.offset);'''

NEW = '''                "videoscale ! "
                "{}",
                _config.frameWidth,
                _config.frameHeight,
                _config.frameRate.numerator,
                _config.frameRate.denominator,
                (std::getenv("MXL_GST_VIDEO_SINK") != nullptr)
                    ? std::string(std::getenv("MXL_GST_VIDEO_SINK"))
                    : fmt::format("autovideosink ts-offset={}", _config.offset));'''

src = open(PATH).read()
if NEW.split("\n")[6].strip() in src and "MXL_GST_VIDEO_SINK" in src:
    print("patch-sink: already applied")
    sys.exit(0)
if OLD not in src:
    sys.exit("patch-sink: anchor not found in sink.cpp — upstream changed, update patch-sink.py")
src = src.replace(OLD, NEW)
if "#include <cstdlib>" not in src:
    src = src.replace("#include <gst/gstpipeline.h>",
                      "#include <gst/gstpipeline.h>\n#include <cstdlib>", 1)
open(PATH, "w").write(src)
print("patch-sink: applied env-configurable MXL_GST_VIDEO_SINK")

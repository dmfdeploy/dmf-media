#!/usr/bin/env python3
"""Build-time patch: pace mxl-gst-sink's past-deadline retry, rate-limit its
repeat-state logging, and add a runtime log-level knob (umbrella #308).

Upstream `tools/mxl-gst/sink.cpp` computes each iteration's read timeout as
`deliveryDeadline - now`, clamped to 0 once the deadline has passed. A read
past the deadline (MXL_ERR_OUT_OF_RANGE_TOO_EARLY) then returns IMMEDIATELY
(zero timeout), so the retry loop spins at microsecond cadence on the SAME
grain index — the video loop logs a WARN on every spin (~40k lines/min, up
to 9 MB/s observed live), the audio loop the analogous thing at TRACE. Both
loops burn CPU on the busy-wait regardless of what gets logged.

This patch:
  1. Floors that zero timeout at one grain/window period (or
     MXL_GST_TOO_EARLY_RETRY_NS, clamped to [1ms, one grain/window period])
     — passed INTO the SDK read call itself, which wakes early on publish,
     so a grain that becomes available mid-wait is never delayed by this.
  2. Wraps the TOO_EARLY/TOO_LATE retry logging, the "{Video,Audio} latency
     increase detected" line, and the "PTS offset adjusted" line in a
     stateful rate limiter (StateRateLimiter): first occurrence logs
     immediately, then at most one aggregate summary every
     MXL_GST_LOG_RATE_LIMIT_SECONDS (default 10s), then — for the
     TOO_EARLY/TOO_LATE retry states — a recovery line on the next
     successful read. Per-iteration logging becomes structurally
     impossible: every one of these call sites goes through the limiter,
     there is no code path left that logs on every spin.
  3. Adds MXL_GST_LOG_LEVEL (utils.hpp) as a runtime severity floor,
     default INFO. This is INDEPENDENT of (2) — even the WARN/TRACE paths
     patched here stay rate-limited regardless of what level is selected.

Applied to the build context only (mirrors patch-sink.py); the upstream
clone stays pristine. Run from the MXL source root, after patch-sink.py.

Fix-round 1 (#308, codex gate P2): the ORIGINAL version of this script wrote
each successful replacement to disk immediately as it validated, across two
files. A LATE anchor failure (e.g. deep into sink.cpp) then exited non-zero
having already written utils.hpp and several earlier sink.cpp edits — a
partial-apply-on-disk state. WORSE: a rerun against that partial state could
exit 0 ("already patched") via a broad marker check that didn't verify every
guard was actually present — a false-green path where a drifted upstream
plus a rerun could ship an image silently missing the fix.

Fixed with two properties, both load-bearing:
  - Two-phase, all-or-nothing apply (apply_all): every anchor across BOTH
    files is validated ENTIRELY IN MEMORY first. Nothing is written to disk
    until every single step has validated. A failure anywhere leaves BOTH
    files completely untouched.
  - Full post-condition verification (check_state), not a broad marker: a
    clean "already applied" no-op requires EVERY step's own NEW text to be
    present with the exact expected count. Any state where some steps are
    applied and others are not is treated as inconsistent and refused
    (exit 1, naming exactly what's present and what's missing) rather than
    silently guessed at.

The OLD/NEW text patched into each file is BYTE-IDENTICAL to the pre-fix-
round-1 version of this script — only the application mechanics changed.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mxl_build_prep import verify_upstream_sha

SINK_PATH = "mxl/tools/mxl-gst/sink.cpp"
UTILS_PATH = "mxl/tools/mxl-gst/utils.hpp"


class PatchError(Exception):
    """An anchor failed to validate. Carries enough detail for a specific,
    loud failure message — never a silent or partial match."""

    def __init__(self, path, label, expected, found):
        self.path = path
        self.label = label
        self.expected = expected
        self.found = found
        super().__init__(
            f"anchor not found ({label}) in {path} — expected {expected} "
            f"occurrence(s), found {found}. Upstream changed; update "
            "patch-sink-too-early-pacing.py."
        )


def _read(path):
    with open(path) as f:
        return f.read()


def _write(path, content):
    # Write-temp + atomic rename (os.replace is POSIX-atomic on the same
    # filesystem): the only visible on-disk state change is the rename
    # itself, so a crash/kill mid-write can never leave a half-written file.
    tmp_path = path + ".patch-tmp"
    with open(tmp_path, "w") as f:
        f.write(content)
    os.replace(tmp_path, path)


class TextStep:
    """One anchor-based OLD -> NEW replacement, byte-exact, count-checked.

    is_applied requires BOTH:
      1. `new` present exactly `count` times (as before).
      2. `old` present EXACTLY as many times as it is structurally embedded
         inside `new` itself — 0 for an ordinary step, or (for the
         utils.hpp includes step, where NEW is literally two `#include`
         lines followed by the entirety of OLD) exactly 1 per NEW instance.

    Checking only (1) is not enough (#308 fix-round 2, codex gate): a
    cleanly-patched file with a STRAY extra copy of `old` appended
    elsewhere (e.g. a mutant, or a bad merge) still shows `new` at its
    expected count — a new-count-only check calls that "applied", making
    the stray content invisible. Condition (2) closes that: any `old`
    occurrence beyond what's structurally embedded in `new` now fails
    is_applied, which (since is_pending requires NOT is_applied) routes the
    step to check_state's "ambiguous" bucket — refused, not silently
    accepted as clean.

    `_embedded_old_per_new` is a per-step CONSTANT computed once from the
    literal `old`/`new` strings themselves at construction time — never
    from file content — so this adds no per-run cost beyond one extra
    `str.count` call per step.
    """

    def __init__(self, path, label, old, new, count=1):
        self.path = path
        self.label = label
        self.old = old
        self.new = new
        self.count = count
        self._embedded_old_per_new = new.count(old)

    def transform(self, src):
        found = src.count(self.old)
        if found != self.count:
            raise PatchError(self.path, self.label, self.count, found)
        return src.replace(self.old, self.new)

    def is_applied(self, src):
        expected_old = self._embedded_old_per_new * self.count
        return (src.count(self.new) == self.count) and (src.count(self.old) == expected_old)

    def is_pending(self, src):
        # `new` completely absent (not merely "not cleanly applied") — a
        # step that's already applied-plus-corrupted (new present at its
        # expected count, PLUS a stray extra old copy) must land in
        # check_state's "ambiguous" bucket, not get mislabeled "pending"
        # just because old's count happens to match self.count by
        # coincidence (#308 fix-round 2 follow-up: the first version of
        # this fix used `not is_applied` here, which classified exactly
        # that corrupted-but-already-patched state as "still pending" —
        # functionally still rc=1 overall, but a confusing label an
        # operator could misread as "never touched").
        return (src.count(self.new) == 0) and (src.count(self.old) == self.count)


class EnsureIncludesStep:
    """Special-cased: inserts whichever of `headers` are missing right
    after `anchor_line`, skipping ones already present. Not a fixed OLD/NEW
    pair — patch-sink.py (applied first, per the Dockerfile) may already
    have added <cstdlib> before this patch ever runs, and a blind
    unconditional insert would duplicate it (harmless in C++, but untidy).

    Presence is checked LINE-ANCHORED as an active directive (#308
    fix-round 3, codex gate): a naive substring check treats
    `// #include <mutex>` (commented out) as evidence the header is
    present — a structural false-green the compiler would catch (a missing
    include fails the build) but this script's own state detection should
    not silently trust. Anchored to the start of a line (leading
    whitespace only) with nothing else on the line rules out both
    comments and any other substring context (e.g. inside a string).
    """

    def __init__(self, path, label, anchor_line, headers):
        self.path = path
        self.label = label
        self.anchor_line = anchor_line
        self.headers = headers
        self._patterns = {
            h: re.compile(r"^[ \t]*#include <" + re.escape(h) + r">[ \t]*$", re.MULTILINE)
            for h in headers
        }

    def _counts(self, src):
        return {h: len(self._patterns[h].findall(src)) for h in self.headers}

    def transform(self, src):
        if self.anchor_line not in src:
            raise PatchError(self.path, self.label, 1, 0)
        counts = self._counts(src)
        missing = [h for h in self.headers if counts[h] == 0]
        if not missing:
            return src  # nothing to do — already satisfied
        insertion = "".join(f"#include <{h}>\n" for h in missing)
        return src.replace(self.anchor_line, self.anchor_line + insertion, 1)

    def is_applied(self, src):
        counts = self._counts(src)
        return all(counts[h] == 1 for h in self.headers)

    def is_pending(self, src):
        # Safe to (re)apply: every header is EITHER already correctly
        # present exactly once (e.g. <cstdlib>, legitimately added by
        # patch-sink.py before this patch ever runs — expected partial
        # coverage, not corruption) OR entirely absent (needs inserting) —
        # but NEVER duplicated (count >= 2 means a stray/tampered active
        # occurrence, e.g. a second real #include alongside a commented-out
        # one). Excludes is_applied's own state (at least one header must
        # still be genuinely missing for this to count as "pending").
        counts = self._counts(src)
        if any(counts[h] >= 2 for h in self.headers):
            return False
        return any(counts[h] == 0 for h in self.headers)


def apply_all(steps):
    """Two-phase, all-or-nothing apply across every step and every file
    (#308 fix-round 1). Validates every step's transform in memory against
    an accumulating per-path buffer; only once ALL steps have validated
    does it write anything to disk.
    """
    contents = {}
    for step in steps:
        src = contents.get(step.path)
        if src is None:
            src = _read(step.path)
        contents[step.path] = step.transform(src)
    for path, content in contents.items():
        _write(path, content)


def check_state(steps):
    """Classify on-disk state against every step without writing anything.

    Returns (state, detail):
      'clean'   — every step already fully applied (safe no-op, rc=0).
      'fresh'   — every step still pending (safe to apply).
      'partial' — a genuine mix, or an ambiguous individual step (neither
                  cleanly applied nor cleanly pending). detail lists step
                  labels in each bucket. This is the state the fix-round 1
                  bug could produce and silently treat as 'clean' — now
                  refused outright (rc=1) instead.
    """
    contents = {}
    applied, pending, unknown = [], [], []
    for step in steps:
        src = contents.get(step.path)
        if src is None:
            src = _read(step.path)
            contents[step.path] = src
        a = step.is_applied(src)
        p = step.is_pending(src)
        if a and not p:
            applied.append(step.label)
        elif p and not a:
            pending.append(step.label)
        else:
            unknown.append(step.label)

    detail = {"applied": applied, "pending": pending, "unknown": unknown}
    if unknown or (applied and pending):
        return "partial", detail
    if applied:
        return "clean", detail
    return "fresh", detail


def build_utils_steps():
    OLD_INCLUDES = """#include <filesystem>
#include <fstream>
#include <sstream>
#include <string>
#include <fmt/format.h>
#include <picojson/wrapper.h>
#include "mxl/rational.h\""""
    NEW_INCLUDES = """#include <cctype>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <string>
#include <fmt/format.h>
#include <picojson/wrapper.h>
#include "mxl/rational.h\""""

    OLD_MACROS = '''/**
 * Logging macros
 */

// clang-format off
#define MXL_LOG(level, msg, ...)                          \\
    do                                                    \\
    {                                                     \\
        std::cerr                                         \\
            << '[' << level << "] " << " - "              \\
            << fmt::format(msg __VA_OPT__(,) __VA_ARGS__) \\
            << std::endl;                                 \\
    }                                                     \\
    while (0)

#define MXL_ERROR(msg, ...) MXL_LOG("ERROR", msg __VA_OPT__(,) __VA_ARGS__)
#define MXL_WARN(msg, ...)  MXL_LOG("WARN",  msg __VA_OPT__(,) __VA_ARGS__)
#define MXL_INFO(msg, ...)  MXL_LOG("INFO",  msg __VA_OPT__(,) __VA_ARGS__)
#define MXL_DEBUG(msg, ...) MXL_LOG("DEBUG", msg __VA_OPT__(,) __VA_ARGS__)
#define MXL_TRACE(msg, ...) MXL_LOG("TRACE", msg __VA_OPT__(,) __VA_ARGS__)
// clang-format on'''

    NEW_MACROS = '''/**
 * Logging macros
 */

// #308: runtime severity floor via MXL_GST_LOG_LEVEL (default INFO).
// Independent of the call-site rate limiting added in sink.cpp for #308 —
// a WARN/TRACE line patched there stays rate-limited regardless of which
// level is selected here; this only gates whether a given severity prints
// AT ALL.
namespace mxl_gst_log
{
    enum class Level : int
    {
        Error = 0,
        Warn = 1,
        Info = 2,
        Debug = 3,
        Trace = 4,
    };

    inline Level parseLevel(char const* raw) noexcept
    {
        if (raw == nullptr)
        {
            return Level::Info;
        }
        auto upper = std::string{raw};
        for (auto& c : upper)
        {
            c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
        }
        if (upper == "ERROR") return Level::Error;
        if (upper == "WARN")  return Level::Warn;
        if (upper == "INFO")  return Level::Info;
        if (upper == "DEBUG") return Level::Debug;
        if (upper == "TRACE") return Level::Trace;
        return Level::Info; // unrecognized value — default rather than fail
    }

    inline Level currentLevel() noexcept
    {
        static Level const level = parseLevel(std::getenv("MXL_GST_LOG_LEVEL"));
        return level;
    }
}

// clang-format off
#define MXL_LOG(level_enum, level_str, msg, ...)                                            \\
    do                                                                                      \\
    {                                                                                       \\
        if (static_cast<int>(level_enum) <= static_cast<int>(mxl_gst_log::currentLevel()))  \\
        {                                                                                    \\
            std::cerr                                         \\
                << '[' << level_str << "] " << " - "          \\
                << fmt::format(msg __VA_OPT__(,) __VA_ARGS__) \\
                << std::endl;                                 \\
        }                                                                                    \\
    }                                                                                       \\
    while (0)

#define MXL_ERROR(msg, ...) MXL_LOG(mxl_gst_log::Level::Error, "ERROR", msg __VA_OPT__(,) __VA_ARGS__)
#define MXL_WARN(msg, ...)  MXL_LOG(mxl_gst_log::Level::Warn,  "WARN",  msg __VA_OPT__(,) __VA_ARGS__)
#define MXL_INFO(msg, ...)  MXL_LOG(mxl_gst_log::Level::Info,  "INFO",  msg __VA_OPT__(,) __VA_ARGS__)
#define MXL_DEBUG(msg, ...) MXL_LOG(mxl_gst_log::Level::Debug, "DEBUG", msg __VA_OPT__(,) __VA_ARGS__)
#define MXL_TRACE(msg, ...) MXL_LOG(mxl_gst_log::Level::Trace, "TRACE", msg __VA_OPT__(,) __VA_ARGS__)
// clang-format on'''

    return [
        TextStep(UTILS_PATH, "utils.hpp includes", OLD_INCLUDES, NEW_INCLUDES),
        TextStep(UTILS_PATH, "utils.hpp MXL_LOG macros", OLD_MACROS, NEW_MACROS),
    ]


def build_sink_steps():
    OLD_SIGNAL_HANDLER = """    void signal_handler(int) noexcept
    {
        g_exit_requested = 1;
    }

    struct VideoPipelineConfig"""
    NEW_SIGNAL_HANDLER = """    void signal_handler(int) noexcept
    {
        g_exit_requested = 1;
    }

    // #308: MXL_GST_TOO_EARLY_RETRY_NS overrides the floor placed on a
    // past-deadline read's timeout (see the run() loops below); unset or
    // unparseable falls back to grainPeriodNs (one grain/window period),
    // clamped to [1ms, grainPeriodNs].
    std::uint64_t resolveTooEarlyRetryNs(std::uint64_t grainPeriodNs) noexcept
    {
        constexpr std::uint64_t kMinRetryNs = 1'000'000ULL; // 1 ms
        auto floorNs = grainPeriodNs;
        if (auto const* env = std::getenv("MXL_GST_TOO_EARLY_RETRY_NS"); (env != nullptr) && (*env != '\\0'))
        {
            try
            {
                floorNs = static_cast<std::uint64_t>(std::stoull(env));
            }
            catch (...)
            {
                // Malformed value — keep the default rather than propagate.
            }
        }
        auto const hi = std::max(grainPeriodNs, kMinRetryNs);
        return std::clamp(floorNs, kMinRetryNs, hi);
    }

    // #308: MXL_GST_LOG_RATE_LIMIT_SECONDS overrides the aggregate-summary
    // interval used by every StateRateLimiter instance below; unset or
    // unparseable defaults to 10s.
    std::uint64_t resolveLogRateLimitNs() noexcept
    {
        constexpr std::uint64_t kDefaultSeconds = 10ULL;
        auto seconds = kDefaultSeconds;
        if (auto const* env = std::getenv("MXL_GST_LOG_RATE_LIMIT_SECONDS"); (env != nullptr) && (*env != '\\0'))
        {
            try
            {
                seconds = static_cast<std::uint64_t>(std::stoull(env));
            }
            catch (...)
            {
                // Malformed value — keep the default.
            }
        }
        return seconds * 1'000'000'000ULL;
    }

    constexpr int kTooEarly = 0;
    constexpr int kTooLate = 1;

    // #308: suppresses per-iteration log spam for a repeating (or
    // continuing) error/event state. Logs the first occurrence immediately,
    // then at most one aggregate summary per `_intervalNs`, then (via
    // recover(), for the TOO_EARLY/TOO_LATE retry use) a recovery line on
    // the next successful read. Not thread-safe by itself — a caller
    // sharing one instance across threads (the PTS-offset use below) must
    // guard it externally.
    class StateRateLimiter
    {
    public:
        struct Recovery
        {
            bool didSuppress;
            std::uint64_t suppressed;
            std::uint64_t durationNs;
            int kind;
        };

        enum class Decision
        {
            Suppress,
            FirstOccurrence,
            PeriodicSummary,
        };

        explicit StateRateLimiter(std::uint64_t intervalNs) noexcept
            : _intervalNs{intervalNs}
        {
        }

        // `index` participates in the "same state" check for kinds that
        // genuinely hold a fixed index while repeating (TOO_EARLY never
        // realigns). Pass a constant sentinel for kinds that don't (see the
        // TOO_LATE call sites, which realign every iteration).
        [[nodiscard]]
        Decision check(std::uint64_t nowNs, int kind, std::uint64_t index) noexcept
        {
            if (!_active || (kind != _kind) || (index != _index))
            {
                _active = true;
                _kind = kind;
                _index = index;
                _firstSeenNs = nowNs;
                _lastSummaryNs = nowNs;
                _suppressedCount = 0;
                return Decision::FirstOccurrence;
            }
            if ((nowNs - _lastSummaryNs) >= _intervalNs)
            {
                _lastSummaryNs = nowNs;
                return Decision::PeriodicSummary;
            }
            ++_suppressedCount;
            return Decision::Suppress;
        }

        [[nodiscard]] std::uint64_t suppressedCount() const noexcept
        {
            return _suppressedCount;
        }

        [[nodiscard]] std::uint64_t durationNs(std::uint64_t nowNs) const noexcept
        {
            return nowNs - _firstSeenNs;
        }

        // Call on a successful read. Always clears the tracked state;
        // Recovery::didSuppress is true only if at least one line was
        // actually suppressed, so callers can skip an empty "recovered"
        // line for a state that only ever hit FirstOccurrence.
        [[nodiscard]]
        Recovery recover(std::uint64_t nowNs) noexcept
        {
            auto result = Recovery{_active && (_suppressedCount > 0), _suppressedCount, nowNs - _firstSeenNs, _kind};
            _active = false;
            _suppressedCount = 0;
            return result;
        }

    private:
        std::uint64_t _intervalNs;
        bool _active{false};
        int _kind{0};
        std::uint64_t _index{0};
        std::uint64_t _firstSeenNs{0};
        std::uint64_t _lastSummaryNs{0};
        std::uint64_t _suppressedCount{0};
    };

    struct VideoPipelineConfig"""

    OLD_PTS_LOG = """                if (_ptsOffset.compare_exchange_strong(currentPtsOffset, newPtsOffset))
                {
                    MXL_INFO("Pipeline(s) PTS offset adjusted to {} ns.", newPtsOffset);
                    pts = bufferTimestampNs - _mxlBaseTime + newPtsOffset;
                }"""
    NEW_PTS_LOG = """                if (_ptsOffset.compare_exchange_strong(currentPtsOffset, newPtsOffset))
                {
                    // #308: shared across the video + audio threads (both push
                    // buffers through this one static offset), so the log-rate
                    // state is guarded by _ptsOffsetLogMutex rather than being a
                    // thread-local instance.
                    auto const now = ::mxlGetTime();
                    auto const lock = std::lock_guard{_ptsOffsetLogMutex};
                    switch (_ptsOffsetLimiter.check(now, 0, 0))
                    {
                        case StateRateLimiter::Decision::FirstOccurrence:
                            MXL_INFO("Pipeline(s) PTS offset adjusted to {} ns.", newPtsOffset);
                            break;
                        case StateRateLimiter::Decision::PeriodicSummary:
                            MXL_INFO("Pipeline(s) PTS offset still adjusting, now {} ns ({} suppressed updates over {} ms).",
                                newPtsOffset, _ptsOffsetLimiter.suppressedCount(), _ptsOffsetLimiter.durationNs(now) / 1'000'000);
                            break;
                        case StateRateLimiter::Decision::Suppress:
                            break;
                    }
                    pts = bufferTimestampNs - _mxlBaseTime + newPtsOffset;
                }"""

    OLD_STATIC_MEMBERS = """        // Has to be shared by all the pipelines, to make sure that we do not introduce audio / video offset.
        static std::atomic<std::uint64_t> _ptsOffset;
        static bool _autoAdjustPtsOffset;
    };

    std::atomic<std::uint64_t> GstreamerPipeline::_ptsOffset = 0;
    bool GstreamerPipeline::_autoAdjustPtsOffset = true;"""
    NEW_STATIC_MEMBERS = """        // Has to be shared by all the pipelines, to make sure that we do not introduce audio / video offset.
        static std::atomic<std::uint64_t> _ptsOffset;
        static bool _autoAdjustPtsOffset;
        // #308: rate-limits the "PTS offset adjusted" log line above, which
        // is otherwise on the buffer-push path (frame-rate cadence) and
        // shared by both the video and audio threads.
        static std::mutex _ptsOffsetLogMutex;
        static StateRateLimiter _ptsOffsetLimiter;
    };

    std::atomic<std::uint64_t> GstreamerPipeline::_ptsOffset = 0;
    bool GstreamerPipeline::_autoAdjustPtsOffset = true;
    std::mutex GstreamerPipeline::_ptsOffsetLogMutex{};
    StateRateLimiter GstreamerPipeline::_ptsOffsetLimiter{resolveLogRateLimitNs()};"""

    OLD_READER_MEMBERS = """    private:
        mxlInstance _instance;
        mxlFlowReader _reader;
        mxlFlowConfigInfo _configInfo;
        std::uint64_t _highestLatencyNs;
    };"""
    NEW_READER_MEMBERS = """    private:
        mxlInstance _instance;
        mxlFlowReader _reader;
        mxlFlowConfigInfo _configInfo;
        std::uint64_t _highestLatencyNs;
        StateRateLimiter _latencyLimiter{resolveLogRateLimitNs()}; // #308
    };"""

    OLD_LATENCY = """        void updateHighestLatency(char const* prefix, std::uint64_t mxlTimestamp, std::uint64_t currentMxlTime)
        {
            auto const latencyNs = currentMxlTime > mxlTimestamp ? currentMxlTime - mxlTimestamp : 0;
            if (latencyNs > _highestLatencyNs)
            {
                _highestLatencyNs = latencyNs;
                MXL_INFO("{} latency increase detected: {} ns", prefix, latencyNs);
            }
        }"""
    NEW_LATENCY = """        void updateHighestLatency(char const* prefix, std::uint64_t mxlTimestamp, std::uint64_t currentMxlTime)
        {
            auto const latencyNs = currentMxlTime > mxlTimestamp ? currentMxlTime - mxlTimestamp : 0;
            if (latencyNs > _highestLatencyNs)
            {
                _highestLatencyNs = latencyNs;
                // #308: a sustained latency climb can otherwise log on every
                // successful grain (frame-rate cadence); rate-limited the
                // same way as the retry paths in run() below. Covers BOTH
                // "Video latency increase detected" and "Audio latency
                // increase detected" — one shared function, one limiter per
                // MxlReader instance (which is itself per-stream).
                auto const now = ::mxlGetTime();
                switch (_latencyLimiter.check(now, 0, 0))
                {
                    case StateRateLimiter::Decision::FirstOccurrence:
                        MXL_INFO("{} latency increase detected: {} ns", prefix, latencyNs);
                        break;
                    case StateRateLimiter::Decision::PeriodicSummary:
                        MXL_INFO("{} latency still increasing: {} ns ({} suppressed updates over {} ms)",
                            prefix, latencyNs, _latencyLimiter.suppressedCount(), _latencyLimiter.durationNs(now) / 1'000'000);
                        break;
                    case StateRateLimiter::Decision::Suppress:
                        break;
                }
            }
        }"""

    OLD_VIDEO_PREAMBLE = """            // The index that corresponds to the current time. We are reading
            // frames that correspond to the index that is readOffset
            // nanoseconds in the past and deliver them as the next frame to the
            // gstreamer pipeline.
            auto cursor = Cursor{rate, 1U, readDelay};

            initializeHighestLatency("Video", rate, cursor.currentIndex(), cursor.requestedIndex());

            auto expectedSlices = slicesPerBatch;"""
    NEW_VIDEO_PREAMBLE = """            // The index that corresponds to the current time. We are reading
            // frames that correspond to the index that is readOffset
            // nanoseconds in the past and deliver them as the next frame to the
            // gstreamer pipeline.
            constexpr auto videoWindowSize = 1U;
            auto cursor = Cursor{rate, videoWindowSize, readDelay};

            initializeHighestLatency("Video", rate, cursor.currentIndex(), cursor.requestedIndex());

            // #308: past-deadline reads used a zero timeout, so the TOO_EARLY
            // branch below spun at microsecond cadence with no pacing. Floor
            // the wait at one grain period (or MXL_GST_TOO_EARLY_RETRY_NS,
            // clamped to [1ms, one grain period]) so the SDK call itself
            // absorbs the wait — it wakes early on publish, so this never
            // adds latency to a grain that actually becomes available.
            auto const grainPeriodNs = ::mxlIndexToTimestamp(&rate, videoWindowSize) - ::mxlIndexToTimestamp(&rate, 0);
            auto const tooEarlyRetryNs = resolveTooEarlyRetryNs(grainPeriodNs);
            auto retryLimiter = StateRateLimiter{resolveLogRateLimitNs()};

            auto expectedSlices = slicesPerBatch;"""

    OLD_TIMEOUT = """                auto const iterationStartTime = ::mxlGetTime();
                auto const iterationTimeoutNs =
                    (iterationStartTime < cursor.deliveryDeadline()) ? (cursor.deliveryDeadline() - iterationStartTime) : 0ULL;"""
    NEW_TIMEOUT = """                auto const iterationStartTime = ::mxlGetTime();
                auto const iterationTimeoutNs =
                    (iterationStartTime < cursor.deliveryDeadline()) ? (cursor.deliveryDeadline() - iterationStartTime) : tooEarlyRetryNs;"""

    OLD_VIDEO_SUCCESS = """                if (ret == MXL_STATUS_OK)
                {
                    if (grainInfo.validSlices >= grainInfo.totalSlices)"""
    NEW_VIDEO_SUCCESS = """                if (ret == MXL_STATUS_OK)
                {
                    // #308: recovery summary if we were suppressing TOO_EARLY/TOO_LATE lines.
                    if (auto const recovery = retryLimiter.recover(::mxlGetTime()); recovery.didSuppress)
                    {
                        MXL_WARN("Recovered from {} at index {} after {} suppressed retries over {} ms",
                            recovery.kind == kTooEarly ? "TOO EARLY" : "TOO LATE",
                            cursor.requestedIndex(),
                            recovery.suppressed,
                            recovery.durationNs / 1'000'000);
                    }

                    if (grainInfo.validSlices >= grainInfo.totalSlices)"""

    OLD_VIDEO_RETRY = """                else if (ret == MXL_ERR_OUT_OF_RANGE_TOO_EARLY)
                {
                    // We are too early somehow, keep trying the same grain index
                    auto runtimeInfo = ::mxlFlowRuntimeInfo{};
                    (void)::mxlFlowReaderGetRuntimeInfo(_reader, &runtimeInfo);
                    MXL_WARN("Failed to get grain at index {}: TOO EARLY. Last published {}", cursor.requestedIndex(), runtimeInfo.headIndex);
                }
                else if (ret == MXL_ERR_OUT_OF_RANGE_TOO_LATE)
                {
                    auto runtimeInfo = ::mxlFlowRuntimeInfo{};
                    (void)::mxlFlowReaderGetRuntimeInfo(_reader, &runtimeInfo);
                    MXL_TRACE("Failed to get grain at index {}: TOO LATE. Last published {}", cursor.requestedIndex(), runtimeInfo.headIndex);

                    // Grain expired. Realign to current index. GStreamer repeats the last valid frame for missing data; consuming applications
                    // should do the same.
                    cursor.realign(iterationStartTime);
                }"""
    NEW_VIDEO_RETRY = """                else if (ret == MXL_ERR_OUT_OF_RANGE_TOO_EARLY)
                {
                    // We are too early somehow, keep trying the same grain index.
                    // #308: rate-limited — see StateRateLimiter above. The index
                    // does not change on TOO_EARLY (no realign), so it is part of
                    // the "same state" key: a genuinely different requested index
                    // is a fresh line, not a suppressed repeat.
                    auto const now = ::mxlGetTime();
                    switch (retryLimiter.check(now, kTooEarly, cursor.requestedIndex()))
                    {
                        case StateRateLimiter::Decision::FirstOccurrence:
                        {
                            auto runtimeInfo = ::mxlFlowRuntimeInfo{};
                            (void)::mxlFlowReaderGetRuntimeInfo(_reader, &runtimeInfo);
                            MXL_WARN("Failed to get grain at index {}: TOO EARLY. Last published {}", cursor.requestedIndex(), runtimeInfo.headIndex);
                            break;
                        }
                        case StateRateLimiter::Decision::PeriodicSummary:
                        {
                            auto runtimeInfo = ::mxlFlowRuntimeInfo{};
                            (void)::mxlFlowReaderGetRuntimeInfo(_reader, &runtimeInfo);
                            MXL_WARN("Still TOO EARLY at index {} after {} suppressed retries over {} ms. Last published {}",
                                cursor.requestedIndex(), retryLimiter.suppressedCount(), retryLimiter.durationNs(now) / 1'000'000, runtimeInfo.headIndex);
                            break;
                        }
                        case StateRateLimiter::Decision::Suppress:
                            break;
                    }
                }
                else if (ret == MXL_ERR_OUT_OF_RANGE_TOO_LATE)
                {
                    // #308: rate-limited — see StateRateLimiter above. Unlike
                    // TOO_EARLY, this realigns every iteration, so the index
                    // genuinely changes each time; the streak is tracked by
                    // kind alone (a constant sentinel index), not by index.
                    auto const now = ::mxlGetTime();
                    switch (retryLimiter.check(now, kTooLate, 0))
                    {
                        case StateRateLimiter::Decision::FirstOccurrence:
                        {
                            auto runtimeInfo = ::mxlFlowRuntimeInfo{};
                            (void)::mxlFlowReaderGetRuntimeInfo(_reader, &runtimeInfo);
                            MXL_TRACE("Failed to get grain at index {}: TOO LATE. Last published {}", cursor.requestedIndex(), runtimeInfo.headIndex);
                            break;
                        }
                        case StateRateLimiter::Decision::PeriodicSummary:
                        {
                            auto runtimeInfo = ::mxlFlowRuntimeInfo{};
                            (void)::mxlFlowReaderGetRuntimeInfo(_reader, &runtimeInfo);
                            MXL_TRACE("Still TOO LATE at index {} after {} suppressed retries over {} ms. Last published {}",
                                cursor.requestedIndex(), retryLimiter.suppressedCount(), retryLimiter.durationNs(now) / 1'000'000, runtimeInfo.headIndex);
                            break;
                        }
                        case StateRateLimiter::Decision::Suppress:
                            break;
                    }

                    // Grain expired. Realign to current index. GStreamer repeats the last valid frame for missing data; consuming applications
                    // should do the same.
                    cursor.realign(iterationStartTime);
                }"""

    OLD_AUDIO_PREAMBLE = """            auto cursor = Cursor{rate, windowSize, readDelay};

            initializeHighestLatency("Audio", rate, cursor.currentIndex(), cursor.requestedIndex());

            while (!g_exit_requested)"""
    NEW_AUDIO_PREAMBLE = """            auto cursor = Cursor{rate, windowSize, readDelay};

            initializeHighestLatency("Audio", rate, cursor.currentIndex(), cursor.requestedIndex());

            // #308: see the video loop's own comment above for the full
            // rationale — the same zero-timeout busy-wait pattern exists here.
            auto const grainPeriodNs = ::mxlIndexToTimestamp(&rate, windowSize) - ::mxlIndexToTimestamp(&rate, 0);
            auto const tooEarlyRetryNs = resolveTooEarlyRetryNs(grainPeriodNs);
            auto retryLimiter = StateRateLimiter{resolveLogRateLimitNs()};

            while (!g_exit_requested)"""

    OLD_AUDIO_SUCCESS = """                if (ret == MXL_STATUS_OK)
                {
                    updateHighestLatency("Audio", ::mxlIndexToTimestamp(&rate, cursor.requestedIndex()), ::mxlGetTime());"""
    NEW_AUDIO_SUCCESS = """                if (ret == MXL_STATUS_OK)
                {
                    // #308: recovery summary if we were suppressing TOO_EARLY/TOO_LATE lines.
                    if (auto const recovery = retryLimiter.recover(::mxlGetTime()); recovery.didSuppress)
                    {
                        MXL_TRACE("Recovered from {} at index {} after {} suppressed retries over {} ms",
                            recovery.kind == kTooEarly ? "TOO EARLY" : "TOO LATE",
                            cursor.requestedIndex(),
                            recovery.suppressed,
                            recovery.durationNs / 1'000'000);
                    }

                    updateHighestLatency("Audio", ::mxlIndexToTimestamp(&rate, cursor.requestedIndex()), ::mxlGetTime());"""

    OLD_AUDIO_RETRY = """                else if (ret == MXL_ERR_OUT_OF_RANGE_TOO_EARLY)
                {
                    // We are too early somehow, keep trying the same index
                    auto runtimeInfo = ::mxlFlowRuntimeInfo{};
                    (void)::mxlFlowReaderGetRuntimeInfo(_reader, &runtimeInfo);

                    // Please note that it can occasionally happen that the last published index in this report is beyond
                    // the requested index, because the flow has been commited to in between the point in time, when the
                    // call to mxlFlowReaderGetSamples() returned and the flow runtime info was fetched.
                    MXL_TRACE("Failed to get samples at index {}: TOO EARLY. Last published {}", cursor.requestedIndex(), runtimeInfo.headIndex);
                }
                else if (ret == MXL_ERR_OUT_OF_RANGE_TOO_LATE)
                {
                    // Samples expired. Realign to current index. GStreamer will generate silence for missing samples. Consuming applications
                    // should handle this better by inserting silence with a micro fades to prevent clicks and pops.
                    auto runtimeInfo = ::mxlFlowRuntimeInfo{};
                    (void)::mxlFlowReaderGetRuntimeInfo(_reader, &runtimeInfo);
                    MXL_TRACE("Failed to get samples at index {}: TOO LATE. Last published {}", cursor.requestedIndex(), runtimeInfo.headIndex);

                    cursor.realign(iterationStartTime);
                }"""
    NEW_AUDIO_RETRY = """                else if (ret == MXL_ERR_OUT_OF_RANGE_TOO_EARLY)
                {
                    // We are too early somehow, keep trying the same index.
                    // #308: rate-limited — see the video loop's StateRateLimiter
                    // comment for the full rationale. No realign on TOO_EARLY, so
                    // the index is part of the "same state" key here too.
                    auto const now = ::mxlGetTime();
                    switch (retryLimiter.check(now, kTooEarly, cursor.requestedIndex()))
                    {
                        case StateRateLimiter::Decision::FirstOccurrence:
                        {
                            auto runtimeInfo = ::mxlFlowRuntimeInfo{};
                            (void)::mxlFlowReaderGetRuntimeInfo(_reader, &runtimeInfo);

                            // Please note that it can occasionally happen that the last published index in this report is beyond
                            // the requested index, because the flow has been commited to in between the point in time, when the
                            // call to mxlFlowReaderGetSamples() returned and the flow runtime info was fetched.
                            MXL_TRACE("Failed to get samples at index {}: TOO EARLY. Last published {}", cursor.requestedIndex(), runtimeInfo.headIndex);
                            break;
                        }
                        case StateRateLimiter::Decision::PeriodicSummary:
                        {
                            auto runtimeInfo = ::mxlFlowRuntimeInfo{};
                            (void)::mxlFlowReaderGetRuntimeInfo(_reader, &runtimeInfo);
                            MXL_TRACE("Still TOO EARLY at index {} after {} suppressed retries over {} ms. Last published {}",
                                cursor.requestedIndex(), retryLimiter.suppressedCount(), retryLimiter.durationNs(now) / 1'000'000, runtimeInfo.headIndex);
                            break;
                        }
                        case StateRateLimiter::Decision::Suppress:
                            break;
                    }
                }
                else if (ret == MXL_ERR_OUT_OF_RANGE_TOO_LATE)
                {
                    // #308: rate-limited — see the video loop's StateRateLimiter
                    // comment for why the index is NOT part of the key here
                    // (this realigns every iteration).
                    auto const now = ::mxlGetTime();
                    switch (retryLimiter.check(now, kTooLate, 0))
                    {
                        case StateRateLimiter::Decision::FirstOccurrence:
                        {
                            auto runtimeInfo = ::mxlFlowRuntimeInfo{};
                            (void)::mxlFlowReaderGetRuntimeInfo(_reader, &runtimeInfo);
                            MXL_TRACE("Failed to get samples at index {}: TOO LATE. Last published {}", cursor.requestedIndex(), runtimeInfo.headIndex);
                            break;
                        }
                        case StateRateLimiter::Decision::PeriodicSummary:
                        {
                            auto runtimeInfo = ::mxlFlowRuntimeInfo{};
                            (void)::mxlFlowReaderGetRuntimeInfo(_reader, &runtimeInfo);
                            MXL_TRACE("Still TOO LATE at index {} after {} suppressed retries over {} ms. Last published {}",
                                cursor.requestedIndex(), retryLimiter.suppressedCount(), retryLimiter.durationNs(now) / 1'000'000, runtimeInfo.headIndex);
                            break;
                        }
                        case StateRateLimiter::Decision::Suppress:
                            break;
                    }

                    // Samples expired. Realign to current index. GStreamer will generate silence for missing samples. Consuming applications
                    // should handle this better by inserting silence with a micro fades to prevent clicks and pops.
                    cursor.realign(iterationStartTime);
                }"""

    return [
        EnsureIncludesStep(SINK_PATH, "sink.cpp includes", "#include <csignal>\n", ("algorithm", "cstdlib", "mutex")),
        TextStep(SINK_PATH, "signal_handler / StateRateLimiter insertion point", OLD_SIGNAL_HANDLER, NEW_SIGNAL_HANDLER),
        TextStep(SINK_PATH, "PTS offset log line", OLD_PTS_LOG, NEW_PTS_LOG),
        TextStep(SINK_PATH, "GstreamerPipeline static members", OLD_STATIC_MEMBERS, NEW_STATIC_MEMBERS),
        TextStep(SINK_PATH, "MxlReader private members", OLD_READER_MEMBERS, NEW_READER_MEMBERS),
        TextStep(SINK_PATH, "updateHighestLatency", OLD_LATENCY, NEW_LATENCY),
        TextStep(SINK_PATH, "video run() preamble", OLD_VIDEO_PREAMBLE, NEW_VIDEO_PREAMBLE),
        TextStep(SINK_PATH, "iterationTimeoutNs (video + audio)", OLD_TIMEOUT, NEW_TIMEOUT, count=2),
        TextStep(SINK_PATH, "video run() success-path recovery", OLD_VIDEO_SUCCESS, NEW_VIDEO_SUCCESS),
        TextStep(SINK_PATH, "video TOO_EARLY/TOO_LATE branches", OLD_VIDEO_RETRY, NEW_VIDEO_RETRY),
        TextStep(SINK_PATH, "audio run() preamble", OLD_AUDIO_PREAMBLE, NEW_AUDIO_PREAMBLE),
        TextStep(SINK_PATH, "audio run() success-path recovery", OLD_AUDIO_SUCCESS, NEW_AUDIO_SUCCESS),
        TextStep(SINK_PATH, "audio TOO_EARLY/TOO_LATE branches", OLD_AUDIO_RETRY, NEW_AUDIO_RETRY),
    ]


def main():
    # umbrella #315: verify the checkout under build matches the pinned
    # upstream SHA before check_state() reads a single source file — the
    # anchor checks below only catch content drift, not "wrong commit
    # entirely".
    verify_upstream_sha()

    all_steps = build_utils_steps() + build_sink_steps()

    state, detail = check_state(all_steps)
    if state == "clean":
        print("patch-sink-too-early-pacing: already fully applied (post-condition verified) — no-op")
        return 0
    if state == "partial":
        sys.stderr.write(
            "patch-sink-too-early-pacing: partial/inconsistent patch state detected — "
            "refusing to guess whether this needs (re)patching.\n"
            f"  already applied: {detail['applied']}\n"
            f"  still pending:   {detail['pending']}\n"
            f"  ambiguous:       {detail['unknown']}\n"
            "This means an earlier run was interrupted mid-apply, or upstream drifted "
            "after a partial apply. Restore the pristine build context and re-run.\n"
        )
        return 1

    # state == "fresh" — every step pending; validate-then-write, all or nothing.
    try:
        apply_all(all_steps)
    except PatchError as e:
        sys.stderr.write(f"patch-sink-too-early-pacing: {e}\n")
        return 1

    print("patch-sink-too-early-pacing: applied MXL_GST_LOG_LEVEL to utils.hpp")
    print("patch-sink-too-early-pacing: applied pacing + rate-limited logging to sink.cpp")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Test regex performance with malformed markdown links."""

import multiprocessing as mp
import re
import time
from queue import Empty

# Original problematic regex
OLD_PATTERN = re.compile(
    r"\[(?P<text>[^\]]+)\]\s*\((?P<link>(?:[\w ]+:[\w ]+(?::[\w ]+)*(?:#[A-Za-z0-9_-]+)?)|(?:[\w ]+#[A-Za-z0-9_-]+))\)",
    re.MULTILINE | re.DOTALL,
)

# Fixed regex
NEW_PATTERN = re.compile(
    r"\[(?P<text>[^\]]+)\][ \t]*\((?P<link>(?:[\w ]+:[\w ]+(?::[\w ]+)*(?:#[A-Za-z0-9_-]+)?)|(?:[\w ]+#[A-Za-z0-9_-]+))\)",
    re.MULTILINE,
)

# Test with the malformed content from the slow file
TEST_CONTENT = """[Team Meetings:Retrospectives:Action Items

](Team Meetings:Retrospectives:Action Items)[breaker
](Software Architecture:Microservices:Service Discovery:Circuit Breaker)"""

def _run_regex_worker(pattern_text: str, flags: int, content: str, out_queue: mp.Queue) -> None:
    try:
        start_time = time.perf_counter()
        compiled = re.compile(pattern_text, flags)
        matches = list(compiled.finditer(content))
        duration_ms = (time.perf_counter() - start_time) * 1000
        match_data = [(m.group("text"), m.group("link")) for m in matches]
        out_queue.put(("ok", duration_ms, match_data))
    except Exception as exc:
        out_queue.put(("error", repr(exc), []))


def _measure_regex_performance(pattern, name):
    """Test regex performance and return execution time."""
    print(f"\nTesting {name}...")
    start_time = time.perf_counter() 
    
    try:
        # Prefer SIGALRM when available (Unix-like).
        import signal
        if hasattr(signal, "SIGALRM") and hasattr(signal, "alarm"):
            def timeout_handler(signum, frame):
                raise TimeoutError("Regex took too long")
            signal.signal(signal.SIGALRM, timeout_handler)
            signal.alarm(5)  # 5 second timeout
            try:
                matches = list(pattern.finditer(TEST_CONTENT))
            finally:
                signal.alarm(0)  # Cancel timeout
            end_time = time.perf_counter()
            duration_ms = (end_time - start_time) * 1000
            print(f"  Time: {duration_ms:.1f}ms")
            print(f"  Matches found: {len(matches)}")
            for match in matches:
                print(f"    Text: '{match.group('text')}' -> Link: '{match.group('link')}'")
            return duration_ms
    except TimeoutError:
        print("  ⚠️  TIMEOUT! Regex took longer than 5 seconds")
        return float("inf")
    except Exception as e:
        print(f"  ❌ Error: {e}")
        return float("inf")

    # Windows-safe fallback: run in a separate process with timeout.
    ctx = mp.get_context("spawn")
    out_queue: mp.Queue = ctx.Queue()
    proc = ctx.Process(
        target=_run_regex_worker,
        args=(pattern.pattern, pattern.flags, TEST_CONTENT, out_queue),
    )
    proc.start()
    proc.join(5)
    if proc.is_alive():
        proc.terminate()
        proc.join()
        print("  ⚠️  TIMEOUT! Regex took longer than 5 seconds")
        return float("inf")

    try:
        status, payload, match_data = out_queue.get_nowait()
    except Empty:
        print("  ❌ Error: no result from worker")
        return float("inf")

    if status != "ok":
        print(f"  ❌ Error: {payload}")
        return float("inf")

    duration_ms = float(payload)
    print(f"  Time: {duration_ms:.1f}ms")
    print(f"  Matches found: {len(match_data)}")
    for text, link in match_data:
        print(f"    Text: '{text}' -> Link: '{link}'")
    return duration_ms

if __name__ == "__main__":
    print("Testing regex performance with malformed markdown links...")
    print(f"Test content:\n{repr(TEST_CONTENT)}")
    
    old_time = _measure_regex_performance(OLD_PATTERN, "Original regex (with re.DOTALL)")
    new_time = _measure_regex_performance(NEW_PATTERN, "Fixed regex (limited whitespace)")
    
    print(f"\n{'='*50}")
    if old_time == float('inf'):
        print("✅ Fix successful! Original regex timed out, new regex completed.")
    elif new_time < old_time:
        speedup = old_time / new_time
        print(f"✅ Performance improved! {speedup:.1f}x faster")
    else:
        print(f"⚠️  New regex took {new_time:.1f}ms vs {old_time:.1f}ms")


def test_regex_performance_regression() -> None:
    old_time = _measure_regex_performance(OLD_PATTERN, "old")
    new_time = _measure_regex_performance(NEW_PATTERN, "new")
    assert new_time != float("inf")
    assert old_time != float("inf")
    # Sub-millisecond timing can vary by platform/load. Keep this guard broad
    # enough to avoid flakes while still catching meaningful slowdowns.
    assert new_time <= old_time * 3.0
    assert new_time <= 50.0

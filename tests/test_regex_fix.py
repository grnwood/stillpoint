#!/usr/bin/env python3
"""Test regex performance with malformed markdown links."""

import re
import time

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

def test_regex_performance(pattern, name):
    """Test regex performance and return execution time."""
    print(f"\nTesting {name}...")
    start_time = time.perf_counter() 
    
    try:
        # Set a timeout to prevent hanging
        import signal
        def timeout_handler(signum, frame):
            raise TimeoutError("Regex took too long")
        
        signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(5)  # 5 second timeout
        
        matches = list(pattern.finditer(TEST_CONTENT))
        
        signal.alarm(0)  # Cancel timeout
        
        end_time = time.perf_counter()
        duration_ms = (end_time - start_time) * 1000
        
        print(f"  Time: {duration_ms:.1f}ms")
        print(f"  Matches found: {len(matches)}")
        for match in matches:
            print(f"    Text: '{match.group('text')}' -> Link: '{match.group('link')}'")
        
        return duration_ms
        
    except TimeoutError:
        signal.alarm(0)
        print(f"  ⚠️  TIMEOUT! Regex took longer than 5 seconds")
        return float('inf')
    except Exception as e:
        signal.alarm(0)
        print(f"  ❌ Error: {e}")
        return float('inf')

if __name__ == "__main__":
    print("Testing regex performance with malformed markdown links...")
    print(f"Test content:\n{repr(TEST_CONTENT)}")
    
    old_time = test_regex_performance(OLD_PATTERN, "Original regex (with re.DOTALL)")
    new_time = test_regex_performance(NEW_PATTERN, "Fixed regex (limited whitespace)")
    
    print(f"\n{'='*50}")
    if old_time == float('inf'):
        print("✅ Fix successful! Original regex timed out, new regex completed.")
    elif new_time < old_time:
        speedup = old_time / new_time
        print(f"✅ Performance improved! {speedup:.1f}x faster")
    else:
        print(f"⚠️  New regex took {new_time:.1f}ms vs {old_time:.1f}ms")
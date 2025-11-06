import time
from concurrent.futures import ThreadPoolExecutor

executor = ThreadPoolExecutor(max_workers=2)
_future1 = None  # module-level state

def inner_func1(name):
    print(f"{name} starting...")
    time.sleep(2)
    print(f"{name} done!")
    return f"{name} result value"

def inner_func2(name):
    print(f"{name} starting...")
    time.sleep(2)
    print(f"{name} done!")

def main(consume_previous=False):
    global _future1

    if not consume_previous:
        # First call: kick off work and return immediately.
        print("Main starting (kickoff)…")
        if _future1 is None:
            _future1 = executor.submit(inner_func1, "Thread 1")
        executor.submit(inner_func2, "Thread 2")
        print("Main finished quickly (did not wait)")
        return "Main result (kickoff)"
    else:
        # Next call: wait for the previous result if it exists.
        print("Main starting (consume)…")
        if _future1 is None:
            print("Nothing to consume.")
            return None
        result = _future1.result()  # blocks only here, on the next call
        _future1 = None             # clear so you can start a new cycle
        print("Main consumed result:", result)
        return result

if __name__ == "__main__":
    r1 = main()                   # starts threads; returns immediately
    print("Got:", r1)

    # …do other stuff here…

    r2 = main(consume_previous=True)  # later call: now we block to get value
    print("Got later:", r2)

import json
import threading
import queue
from flask import Response, stream_with_context


def run_sse_search(search_func, *args, **kwargs):
    """Run a search function in a background thread and return an SSE response.

    The search_func receives (q, stop_event, *args, **kwargs) and must put
    dict values on q to yield SSE data events. When finished the function
    returns; a None sentinel is injected automatically to end the stream.
    """
    q = queue.Queue()
    stop_event = threading.Event()

    def worker():
        try:
            search_func(q, stop_event, *args, **kwargs)
        except Exception as e:
            q.put({"type": "error", "message": str(e)})
        finally:
            q.put(None)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    def generate():
        while True:
            result = q.get()
            if result is None:
                break
            yield f"data: {json.dumps(result)}\n\n"
        q.task_done()

    return Response(stream_with_context(generate()), mimetype="text/event-stream")

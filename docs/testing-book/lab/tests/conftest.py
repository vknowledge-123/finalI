import asyncio
import socket
import threading
import time

import pytest
import uvicorn
from fastapi.testclient import TestClient
from server import create_app


@pytest.fixture
def client():
    with TestClient(create_app()) as value:
        yield value


@pytest.fixture
def lab_url():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(), log_level="error"))
    thread = threading.Thread(target=lambda: asyncio.run(server.serve(sockets=[sock])))
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("Lab server did not start")
            time.sleep(.02)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()
        assert not thread.is_alive(), "Lab server failed to stop"

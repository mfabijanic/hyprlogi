import asyncio

from hyprlogi import control


def test_request_round_trip(tmp_path):
    socket_path = tmp_path / "control.sock"
    seen = []

    async def handler(req):
        seen.append(req)
        if req["cmd"] == "boom":
            raise RuntimeError("nope")
        return {"ok": True, "echo": req["cmd"]}

    async def scenario():
        assert await control.request({"cmd": "status"}, socket_path) is None
        server = await control.serve(handler, socket_path)
        try:
            assert await control.request({"cmd": "status"}, socket_path) == {"ok": True, "echo": "status"}
            reply = await control.request({"cmd": "boom"}, socket_path)
            assert reply["ok"] is False and "nope" in reply["error"]
            # A second daemon must not steal the socket
            try:
                await control.serve(handler, socket_path)
                raise AssertionError("expected DaemonRunning")
            except control.DaemonRunning:
                pass
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())
    assert [r["cmd"] for r in seen] == ["status", "boom", "status"]


def test_stale_socket_is_replaced(tmp_path):
    socket_path = tmp_path / "control.sock"
    socket_path.touch()

    async def scenario():
        server = await control.serve(lambda req: asyncio.sleep(0, {"ok": True}), socket_path)
        server.close()

    asyncio.run(scenario())


def test_pause_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(control, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(control, "PAUSED_FLAG", tmp_path / "paused")
    assert not control.is_paused()
    control.set_paused(True)
    assert control.is_paused()
    control.set_paused(False)
    control.set_paused(False)
    assert not control.is_paused()

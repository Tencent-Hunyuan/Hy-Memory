# -*- coding: utf-8 -*-
"""Owned event-loop threads must be stopped and closed with the client."""

from hy_memory.client import HyMemoryClient, _LoopThread


def test_loop_thread_stop_closes_the_loop_and_is_idempotent():
    loop_thread = _LoopThread()
    loop = loop_thread._loop

    loop_thread.stop()
    assert not loop_thread._thread.is_alive()
    assert loop.is_closed()

    # A second stop must not touch a closed loop.
    loop_thread.stop()


def test_close_stops_both_loops_owned_by_a_solo_client():
    client = HyMemoryClient.__new__(HyMemoryClient)
    client._registry = None
    client._owns_runtime = True
    client._loop_thread = _LoopThread()
    client._search_loop_thread = _LoopThread()
    loops = [client._loop_thread._loop, client._search_loop_thread._loop]

    client.close()

    assert all(loop.is_closed() for loop in loops)


def test_shared_runtime_loops_are_left_to_the_runtime():
    client = HyMemoryClient.__new__(HyMemoryClient)
    client._registry = None
    client._owns_runtime = False
    client._loop_thread = _LoopThread()
    client._search_loop_thread = _LoopThread()
    loops = [client._loop_thread._loop, client._search_loop_thread._loop]

    try:
        client.close()
        assert all(not loop.is_closed() for loop in loops)
    finally:
        client._loop_thread.stop()
        client._search_loop_thread.stop()

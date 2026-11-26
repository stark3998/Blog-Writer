"""The broker: fan-out, filters, drop-oldest deltas, never-dropped job events, limits."""

from __future__ import annotations

import asyncio

import pytest

from blogwriter.jobs.broker import (
    MAX_DELTA_CHARS,
    Broker,
    BrokerFull,
    DeltaMessage,
    JobEventMessage,
    Message,
    Subscription,
)

STAMP = "2026-11-26T12:00:00.000000Z"


def event(job_id: str, seq: int) -> JobEventMessage:
    return JobEventMessage(
        job_id=job_id, seq=seq, at=STAMP, type="status", step=None, status="running"
    )


async def drain(subscription: Subscription) -> list[Message]:
    items: list[Message] = []
    while (item := await subscription.get(max_wait=0)) is not None:
        items.append(item)
    return items


async def test_messages_fan_out_to_interested_subscribers() -> None:
    broker = Broker()
    everything = broker.subscribe()
    only_a = broker.subscribe(["job-a"])
    broker.publish(event("job-a", 1))
    broker.publish(event("job-b", 1))
    broker.publish_delta("job-b", "draft", "Hello")
    assert [(m.job_id, type(m).__name__) for m in await drain(everything)] == [
        ("job-a", "JobEventMessage"),
        ("job-b", "JobEventMessage"),
        ("job-b", "DeltaMessage"),
    ]
    assert [m.job_id for m in await drain(only_a)] == ["job-a"]
    assert only_a.job_ids == frozenset({"job-a"})
    assert everything.job_ids is None


async def test_get_waits_for_a_message_or_times_out() -> None:
    broker = Broker()
    subscription = broker.subscribe()
    assert await subscription.get(max_wait=0.01) is None
    waiter = asyncio.create_task(subscription.get(max_wait=5))
    await asyncio.sleep(0)
    broker.publish(event("job-a", 1))
    assert await waiter == event("job-a", 1)


async def test_token_deltas_are_dropped_oldest_first_and_flag_the_gap() -> None:
    broker = Broker(queue_size=4)
    subscription = broker.subscribe()
    broker.publish(event("job-a", 1))
    for number in range(6):
        broker.publish_delta("job-a", "draft", f"t{number}")
    items = await drain(subscription)
    assert items[0] == event("job-a", 1)
    deltas = [item for item in items if isinstance(item, DeltaMessage)]
    assert [delta.text for delta in deltas] == ["t3", "t4", "t5"]
    assert [delta.gap for delta in deltas] == [True, False, False]
    assert subscription.dropped_deltas == 3
    assert not subscription.closed


async def test_job_events_are_never_dropped_for_deltas() -> None:
    broker = Broker(queue_size=3)
    subscription = broker.subscribe()
    broker.publish_delta("job-a", "draft", "t0")
    broker.publish_delta("job-a", "draft", "t1")
    broker.publish(event("job-a", 1))
    broker.publish(event("job-a", 2))  # full: the oldest delta makes room
    broker.publish(event("job-a", 3))  # full again: the other delta makes room
    items = await drain(subscription)
    assert items == [event("job-a", 1), event("job-a", 2), event("job-a", 3)]
    assert subscription.dropped_deltas == 2


async def test_a_delta_arriving_at_a_queue_full_of_events_is_the_one_dropped() -> None:
    broker = Broker(queue_size=2)
    subscription = broker.subscribe()
    broker.publish(event("job-a", 1))
    broker.publish(event("job-a", 2))
    broker.publish_delta("job-a", "draft", "lost")
    assert len(subscription) == 2
    assert not subscription.closed
    await drain(subscription)
    broker.publish_delta("job-a", "draft", "next")
    item = await subscription.get(max_wait=0)
    assert isinstance(item, DeltaMessage)
    assert item.gap


async def test_a_subscriber_full_of_job_events_is_closed_instead_of_losing_one() -> None:
    broker = Broker(queue_size=2)
    slow = broker.subscribe()
    fast = broker.subscribe()
    broker.publish(event("job-a", 1))
    broker.publish(event("job-a", 2))
    assert await fast.get(max_wait=0) == event("job-a", 1)
    broker.publish(event("job-a", 3))
    assert slow.closed
    assert slow.close_reason == "overflow"
    assert await slow.get(max_wait=0) is None
    assert broker.subscriber_count == 1
    assert await drain(fast) == [event("job-a", 2), event("job-a", 3)]


async def test_subscriber_limit_and_unsubscribe() -> None:
    broker = Broker(max_subscribers=2)
    first = broker.subscribe()
    async with broker.subscribe():
        with pytest.raises(BrokerFull):
            broker.subscribe()
    assert broker.subscriber_count == 1
    broker.subscribe()
    first.close()
    first.close()  # idempotent
    assert broker.subscriber_count == 1


async def test_close_ends_every_subscription() -> None:
    broker = Broker()
    subscription = broker.subscribe()
    waiter = asyncio.create_task(subscription.get(max_wait=5))
    await asyncio.sleep(0)
    broker.close()
    assert await waiter is None
    assert subscription.close_reason == "shutdown"
    broker.publish(event("job-a", 1))
    late = broker.subscribe()
    assert late.closed
    assert late.close_reason == "shutdown"
    assert broker.closed


async def test_delta_rules() -> None:
    broker = Broker()
    subscription = broker.subscribe()
    broker.publish_delta("job-a", "draft", "")
    assert len(subscription) == 0
    with pytest.raises(ValueError, match="at most"):
        broker.publish_delta("job-a", "draft", "x" * (MAX_DELTA_CHARS + 1))
    with pytest.raises(ValueError, match="positive"):
        Broker(queue_size=0)


def test_payloads_carry_ids_and_no_surprises() -> None:
    message = event("job-a", 7)
    assert message.event_id == "job-a:7"
    assert message.payload() == {
        "jobId": "job-a",
        "seq": 7,
        "at": STAMP,
        "type": "status",
        "step": None,
        "status": "running",
        "data": {},
    }
    assert DeltaMessage("job-a", "draft", "Hi").payload() == {
        "jobId": "job-a",
        "step": "draft",
        "text": "Hi",
        "gap": False,
    }

# -*- coding: utf-8 -*-
# Copyright 2014 OpenMarket Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


from tests import unittest
from twisted.internet import defer

from synapse.server import HomeServer
from synapse.api.constants import EventTypes, Membership
from synapse.types import UserID

from tests.utils import SQLiteMemoryDbPool, MockKey

from mock import Mock


class RedactionTestCase(unittest.TestCase):

    @defer.inlineCallbacks
    def setUp(self):
        db_pool = SQLiteMemoryDbPool()
        yield db_pool.prepare()

        self.mock_config = Mock()
        self.mock_config.signing_key = [MockKey()]

        hs = HomeServer(
            "test",
            db_pool=db_pool,
            config=self.mock_config,
            resource_for_federation=Mock(),
            http_client=None,
        )

        self.store = hs.get_datastore()
        self.event_builder_factory = hs.get_event_builder_factory()
        self.handlers = hs.get_handlers()
        self.message_handler = self.handlers.message_handler

        self.u_alice = UserID.from_string("@alice:test")
        self.u_bob = UserID.from_string("@bob:test")

        self.room1 = hs.parse_roomid("!abc123:test")

        self.depth = 1

    @defer.inlineCallbacks
    def inject_room_member(self, room, user, membership, replaces_state=None,
                           extra_content={}):
        content = {"membership": membership}
        content.update(extra_content)
        builder = self.event_builder_factory.new({
            "type": EventTypes.Member,
            "sender": user.to_string(),
            "state_key": user.to_string(),
            "room_id": room.to_string(),
            "content": content,
        })

        event, context = yield self.message_handler._create_new_client_event(
            builder
        )

        yield self.store.persist_event(event, context)

        defer.returnValue(event)

    @defer.inlineCallbacks
    def inject_message(self, room, user, body):
        self.depth += 1

        builder = self.event_builder_factory.new({
            "type": EventTypes.Message,
            "sender": user.to_string(),
            "state_key": user.to_string(),
            "room_id": room.to_string(),
            "content": {"body": body, "msgtype": u"message"},
        })

        event, context = yield self.message_handler._create_new_client_event(
            builder
        )

        yield self.store.persist_event(event, context)

        defer.returnValue(event)

    @defer.inlineCallbacks
    def inject_redaction(self, room, event_id, user, reason):
        builder = self.event_builder_factory.new({
            "type": EventTypes.Redaction,
            "sender": user.to_string(),
            "state_key": user.to_string(),
            "room_id": room.to_string(),
            "content": {"reason": reason},
            "redacts": event_id,
        })

        event, context = yield self.message_handler._create_new_client_event(
            builder
        )

        yield self.store.persist_event(event, context)

    @defer.inlineCallbacks
    def test_redact(self):
        yield self.inject_room_member(
            self.room1, self.u_alice, Membership.JOIN
        )

        start = yield self.store.get_room_events_max_id()

        msg_event = yield self.inject_message(self.room1, self.u_alice, u"t")

        end = yield self.store.get_room_events_max_id()

        results, _ = yield self.store.get_room_events_stream(
            self.u_alice.to_string(),
            start,
            end,
            None,  # Is currently ignored
        )

        self.assertEqual(1, len(results))

        # Check event has not been redacted:
        event = results[0]

        self.assertObjectHasAttributes(
            {
                "type": EventTypes.Message,
                "user_id": self.u_alice.to_string(),
                "content": {"body": "t", "msgtype": "message"},
            },
            event,
        )

        self.assertFalse("redacted_because" in event.unsigned)

        # Redact event
        reason = "Because I said so"
        yield self.inject_redaction(
            self.room1, msg_event.event_id, self.u_alice, reason
        )

        results, _ = yield self.store.get_room_events_stream(
            self.u_alice.to_string(),
            start,
            end,
            None,  # Is currently ignored
        )

        self.assertEqual(1, len(results))

        # Check redaction

        event = results[0]

        self.assertEqual(msg_event.event_id, event.event_id)

        self.assertTrue("redacted_because" in event.unsigned)

        self.assertObjectHasAttributes(
            {
                "type": EventTypes.Message,
                "user_id": self.u_alice.to_string(),
                "content": {},
            },
            event,
        )

        self.assertObjectHasAttributes(
            {
                "type": EventTypes.Redaction,
                "user_id": self.u_alice.to_string(),
                "content": {"reason": reason},
            },
            event.unsigned["redacted_because"],
        )

    @defer.inlineCallbacks
    def test_redact_join(self):
        yield self.inject_room_member(
            self.room1, self.u_alice, Membership.JOIN
        )

        start = yield self.store.get_room_events_max_id()

        msg_event = yield self.inject_room_member(
            self.room1, self.u_bob, Membership.JOIN,
            extra_content={"blue": "red"},
        )

        end = yield self.store.get_room_events_max_id()

        results, _ = yield self.store.get_room_events_stream(
            self.u_alice.to_string(),
            start,
            end,
            None,  # Is currently ignored
        )

        self.assertEqual(1, len(results))

        # Check event has not been redacted:
        event = results[0]

        self.assertObjectHasAttributes(
            {
                "type": EventTypes.Member,
                "user_id": self.u_bob.to_string(),
                "content": {"membership": Membership.JOIN, "blue": "red"},
            },
            event,
        )

        self.assertFalse(hasattr(event, "redacted_because"))

        # Redact event
        reason = "Because I said so"
        yield self.inject_redaction(
            self.room1, msg_event.event_id, self.u_alice, reason
        )

        results, _ = yield self.store.get_room_events_stream(
            self.u_alice.to_string(),
            start,
            end,
            None,  # Is currently ignored
        )

        self.assertEqual(1, len(results))

        # Check redaction

        event = results[0]

        self.assertTrue("redacted_because" in event.unsigned)

        self.assertObjectHasAttributes(
            {
                "type": EventTypes.Member,
                "user_id": self.u_bob.to_string(),
                "content": {"membership": Membership.JOIN},
            },
            event,
        )

        self.assertObjectHasAttributes(
            {
                "type": EventTypes.Redaction,
                "user_id": self.u_alice.to_string(),
                "content": {"reason": reason},
            },
            event.unsigned["redacted_because"],
        )

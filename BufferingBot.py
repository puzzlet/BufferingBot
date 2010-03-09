import sys
import time
import heapq
import collections
import traceback

import irclib
import ircbot

class Message: # TODO: irclib.Event?
    """Represents IRC message."""

    def __init__(self, command, arguments, timestamp=None):
        assert isinstance(command, str)
        self.command = command
        self.arguments = arguments
        self.timestamp = time.time() if timestamp is None else timestamp

    def __repr__(self):
        return '<Message %s %s %s>' % (
            repr(self.command),
            repr(self.arguments),
            repr(self.timestamp)
        )

    def __lt__(self, message):
        return self.timestamp < message.timestamp

    def is_system_message(self):
        if self.command in ['privmsg', 'privnotice']:
            return self.arguments[1].startswith('--') # XXX
        return False

class MessageBuffer(object):
    """Buffer of Message objects, sorted by their timestamp.
    If some of its Message's timestamp lags over self.timeout, it purges all
    the queue.
    Note that this uses heapq mechanism hence not thread-safe.
    """

    def __init__(self, timeout=10.0):
        self.timeout = timeout
        self.heap = []

    def __len__(self):
        return len(self.heap)

    def dump(self):
        print(self.heap)

    def peek(self):
        return self.heap[0]

    def push(self, message):
        return heapq.heappush(self.heap, message)

    def _pop(self):
        """[Internal]"""
        if not self.heap:
            return None
        return heapq.heappop(self.heap)

    def pop(self):
        if self.peek().timestamp < time.time() - self.timeout:
            self.purge()
        return self._pop()

    def purge(self):
        if self.timeout < 0:
            return
        stale = time.time() - self.timeout
        line_counts = collections.defaultdict(int)
        while self.heap:
            message = self.peek()
            if message.timestamp > stale:
                break
            if message.command in ['join']: # XXX
                break
            message = self._pop()
            if message.command in ['privmsg', 'privnotice']:
                try:
                    target, str_message = message.arguments
                except Exception:
                    traceback.print_exc()
                    self.push(str_message)
                    return
                if not message.is_system_message():
                    line_counts[target] += 1
        for target, line_count in line_counts.items():
            message = "-- Message lags over %f seconds. Skipping %d line(s).." \
                % (self.timeout, line_count)
            message = Message(
                command = 'privmsg',
                arguments = (target, message)
            )
            self.push(message)

    def has_buffer_by_command(self, command):
        return any(_.command == command for _ in self.heap)

class BufferingBot(ircbot.SingleServerIRCBot):
    """IRC bot with flood buffer.
    Arguments:
        network_list --
        nickname
        realname
        reconnection_interval
        use_ssl
        buffer_timeout -- negative value if you don't want messages to be
                          purged at all.
        passive -- whether you want to call on_tick() from outside.
    """

    def __init__(self, network_list, nickname, realname,
                 reconnection_interval=60, use_ssl=False, buffer_timeout=10.0,
                 passive=False):
        ircbot.SingleServerIRCBot.__init__(self, network_list, nickname,
                                           realname, reconnection_interval,
                                           use_ssl)
        self.buffer = MessageBuffer(timeout=buffer_timeout)
        self.last_tick = 0
        self.passive = passive
        if not passive:
            self.on_tick()

    def on_tick(self):
        self.flood_control()
        if not self.passive:
            self.ircobj.execute_delayed(0.2, self.on_tick)

    def get_delay(self, message):
        # TODO: per-network configuration
        delay = 0
        if message.command in ['privmsg']:
            delay = 2
            try:
                _, msg = message.arguments
                delay = 0.5 + len(msg) / 35.
            except Exception:
                traceback.print_exc()
        if delay > 4:
            delay = 4
        return delay

    def flood_control(self):
        """Delays message according to the length of message.
        As you see, this doesn't acquire any lock hence thread-unsafe.
        """
        if not self.connection.is_connected():
            self._connect()
            return False
        if len(self.buffer):
            self.pop_buffer(self.buffer)
            return True
        return False

    def pop_buffer(self, message_buffer):
        if not message_buffer:
            return
        message = message_buffer.peek()
        if message.command in ['privmsg']:
            try:
                target, _ = message.arguments
                if irclib.is_channel(target) and target not in self.channels:
                    return
            except Exception:
                traceback.print_exc()
                return
        delay = self.get_delay(message)
        tick = time.time()
        if self.last_tick + delay > tick:
            return
        self.process_message(message)
        message_ = message_buffer.pop()
        if message != message_:
            print(message)
            print(message_)
            assert False
        self.last_tick = tick

    def process_message(self, message):
        try:
            if False:
                pass
            elif message.command == 'join':
                self.connection.join(*message.arguments)
            elif message.command == 'mode':
                self.connection.mode(*message.arguments)
            elif message.command == 'privmsg':
                self.connection.privmsg(*message.arguments)
            elif message.command == 'notice':
                self.connection.notice(*message.arguments)
            elif message.command == 'topic':
                self.connection.topic(*message.arguments)
            elif message.command == 'who':
                self.connection.who(*message.arguments)
            elif message.command == 'whois':
                self.connection.whois(*message.arguments)
        except irclib.ServerNotConnectedError:
            self.push_message(message)
            self._connect()
        except:
            traceback.print_exc()
            self.push_message(message)

    def push_message(self, message):
        self.buffer.push(message)


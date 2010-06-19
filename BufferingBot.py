import codecs
import collections
import datetime
import heapq
import time

import ircbot
import irclib

class Message: # TODO: irclib.Event?
    """Represents IRC message."""

    def __init__(self, command, arguments, timestamp=None):
        assert isinstance(command, str)
        self.command = command
        self.arguments = arguments
        self.timestamp = time.time() if timestamp is None else timestamp

    def __repr__(self):
        return '<Message [%s] %s %s>' % (
            time.strftime('%m %d %H:%M:%S', time.localtime(self.timestamp)),
            self.command,
            repr(self.arguments),
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
        heap = self.heap[:]
        while heap:
            yield heapq.heappop(heap)[-1]

    def peek(self):
        return self.heap[0][-1]

    def push(self, message):
        return heapq.heappush(self.heap, (message.timestamp, time.time(), message))

    def _pop(self):
        """[Internal]"""
        if not self.heap:
            return None
        return heapq.heappop(self.heap)[-1]

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
                target = message.arguments[0]
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
        return any(_[-1].command == command for _ in self.heap)

class BufferingBot(ircbot.SingleServerIRCBot):
    """IRC bot with flood buffer.
    Arguments:
        network_list --
        nickname
        username
        realname
        reconnection_interval
        use_ssl
        buffer_timeout -- negative value if you don't want messages to be
                          purged at all.
        passive -- whether you want to call on_tick() from outside.
    """

    def __init__(self, network_list, nickname, username=None, realname=None,
                 reconnection_interval=60, use_ssl=False,
                 codec=None, buffer_timeout=10.0, passive=False):
        ircbot.SingleServerIRCBot.__init__(self, network_list, nickname,
            username=username, realname=realname,
            reconnection_interval=reconnection_interval, use_ssl=use_ssl)
        self.codec = codec
        if not self.codec:
            self.codec = codecs.lookup('utf8')
        self.buffer = MessageBuffer(timeout=buffer_timeout)
        self.last_tick = 0
        self.passive = passive
        if not passive:
            self.on_tick()

    def on_tick(self):
        self.flood_control()
        if not self.passive:
            self.ircobj.execute_delayed(0.1, self.on_tick)

    def get_delay(self, message):
        # TODO: per-network configuration
        delay = 0
        if message.command in ['privmsg']:
            delay = 2
            str_message = self.codec.encode(message.arguments[1])
            delay = 0.5 + len(str_message) / 35.
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
            return False
        message = message_buffer.peek()
        if message.command in ['privmsg']:
            target = message.arguments[0]
            chan = self.codec.encode(target)[0]
            if irclib.is_channel(chan) and chan not in self.channels:
                return False
        delay = self.get_delay(message)
        tick = time.time()
        if self.last_tick + delay > tick:
            return False
        self.process_message(message)
        message_ = message_buffer.pop()
        if message != message_:
            print(message)
            print(message_)
            assert False
        self.last_tick = tick
        return True

    def process_message(self, message):
        if message.command not in irclib.all_events:
            return False
        fun = getattr(self.connection, message.command, None)
        if fun is None:
            return False
        arguments = [self.codec.encode(_)[0] for _ in message.arguments]
        try:
            fun(*arguments)
        except irclib.ServerNotConnectedError:
            self.push_message(message)
            self._connect()
            return False
        return True

    def push_message(self, message):
        self.buffer.push(message)


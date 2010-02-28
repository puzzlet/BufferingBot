import time
import heapq
import collections
import traceback

import irclib
import ircbot

def periodic(period):
    """Decorate a class instance method so that the method would be
    periodically executed by irclib framework.
    """
    def decorator(f):
        def new_f(self, *args):
            try:
                f(self, *args)
            except StopIteration:
                return
            finally:
                self.ircobj.execute_delayed(period, new_f, (self,) + args)
        return new_f
    return decorator

class Message():
    def __init__(self, command, arguments, timestamp=None):
        assert isinstance(command, str)
        self.command = command
        self.arguments = []
        for _ in arguments:
            self.arguments.append(_ if isinstance(_, bytes) else _.encode('utf-8'))
        self.timestamp = time.time() if timestamp is None else timestamp

    def __repr__(self):
        return '<Message %s %s %s>' % (
            repr(self.command),
            repr(self.arguments),
            repr(self.timestamp)
        )

    def __cmp__(self, message):
        return cmp(self.timestamp, message.timestamp)

    def __lt__(self, message):
        return self.timestamp < message.timestamp

    def is_system_message(self):
        if self.command in ['privmsg', 'privnotice']:
            return self.arguments[1].startswith('--') # XXX
        return False

class MessageBuffer(object):
    """Buffer of Message objects, sorted by their timestamp.
    If some of its Message's timestamp lags over self.timeout, it purges all the queue.
    Note that this uses heapq mechanism hence not thread-safe.
    """

    def __init__(self, timeout=10.0):
        self.timeout = timeout
        self.heap = []

    def __len__(self):
        return len(self.heap)

    def _dump(self):
        print(self.heap)

    def peek(self):
        return self.heap[0]

    def push(self, message):
        return heapq.heappush(self.heap, message)

    def _pop(self):
        if not self.heap:
            return None
        return heapq.heappop(self.heap)

    def pop(self):
        if self.peek().timestamp < time.time() - self.timeout:
            self.purge()
        return self._pop()

    def purge(self):
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
                    target, message = message.arguments
                except:
                    traceback.print_exc()
                    self.push(message)
                    return
                if not self.is_system_message(message):
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

    def is_system_message(self, message):
        return message.startswith('--') # XXX

class BufferingBot(ircbot.SingleServerIRCBot):
    def __init__(self, network_list, nickname, realname,
                 reconnection_interval=60, use_ssl=False):
        ircbot.SingleServerIRCBot.__init__(self, network_list, nickname,
                                           realname, reconnection_interval,
                                           use_ssl)
        self.buffer = MessageBuffer(10.0)
        self.last_tick = 0
        self.on_tick()

    @periodic(0.2)
    def on_tick(self):
        if not self.connection.is_connected():
            return
        self.flood_control()

    def get_delay(self, message):
        # TODO: per-network configuration
        delay = 0
        if message.command == 'privmsg':
            delay = 2
            try:
                target, msg = message.arguments
                delay = 0.5 + len(msg) / 35.
            except:
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
            return
        message = None
        local = False
        if len(self.buffer):
            print('--- buffer ---')
            self.buffer._dump()
            self.pop_buffer(self.buffer)

    def pop_buffer(self, buffer):
        if not buffer:
            return
        message = buffer.peek()
        if message.command == 'privmsg':
            try:
                target, msg = message.arguments
                if irclib.is_channel(target) and target not in self.channels:
                    return
            except:
                traceback.print_exc()
                return
        delay = self.get_delay(message)
        tick = time.time()
        if self.last_tick + delay > tick:
            return
        self.process_message(message)
        message_ = buffer.pop()
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
            elif message.command == 'privnotice':
                self.connection.privnotice(*message.arguments)
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


"""A bounded stdio session with one owned tool request at a time."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import json
import os
import queue
import sys
import threading

from .procs import CommandCancelled, own_processes


class _OutputClosed(Exception):
    """The client stopped accepting protocol output."""


def _lines(source):
    try:
        descriptor = source.fileno()
    except (AttributeError, OSError):
        yield from iter(source.readline, '')
        return
    pending = b''
    while chunk := os.read(descriptor, 65536):
        pieces = (pending + chunk).split(b'\n')
        pending = pieces.pop()
        yield from (piece.decode('utf-8') for piece in pieces)
    if pending:
        yield pending.decode('utf-8')


def _read(source, events, slots):
    try:
        for line in _lines(source):
            slots.acquire()
            events.put(('input', line))
    except (OSError, ValueError):
        pass
    finally:
        events.put(('eof', None))


class Session:
    """Keep protocol input responsive while a single CLI child is running."""

    def __init__(self, source, target, reply, run_cli):
        self.source, self.target = source, target
        self.reply, self.run_cli = reply, run_cli
        self.events = queue.SimpleQueue()
        self.input_slots = threading.Semaphore(32)
        self.stack = ExitStack()
        self.owner_context = None
        self.lock = threading.Lock()
        self.owner = self.pool = self.active = None
        self.request_id = None

    def run(self):
        threading.Thread(target=_read, args=(self.source, self.events, self.input_slots),
                         daemon=True).start()
        try:
            while self._event(*self.events.get()):
                pass
        finally:
            self._cleanup()
        return 0

    def _cleanup(self):
        failure = sys.exception()
        for cleanup in (self._cancel_for_cleanup, self.stack.close, self._retire_owner):
            try:
                cleanup()
            except BaseException as error:
                failure = failure or error
        if failure is not None:
            raise failure

    def _cancel_for_cleanup(self):
        try:
            self._cancel()
        except BaseException as error:
            try:
                self._retire_owner()
            finally:
                raise error

    def _event(self, kind, value):
        if kind == 'eof':
            return False
        if kind == 'done':
            self._done(value)
        else:
            self.input_slots.release()
            self._input(value)
        return True

    def _input(self, line):
        try:
            message = json.loads(line)
        except ValueError:
            return
        if not isinstance(message, dict):
            return
        if message.get('method') == 'notifications/cancelled' and 'id' not in message:
            self._notification(message)
            return
        self.request_id = message.get('id')
        response = self.reply(message, self._submit)
        if self._immediate(response):
            self._write(response)

    def _immediate(self, response):
        if response is None:
            return False
        return self.active is None or response.get('result') is not self.active

    def _notification(self, message):
        params = message.get('params') or {}
        if not isinstance(params, dict):
            return
        if self._matches(params.get('requestId')):
            self._cancel()

    def _matches(self, request_id):
        if self.active is None:
            return False
        current = self.active['id']
        return type(request_id) is type(current) and request_id == current

    def _submit(self, tool, arguments, repo):
        if self.active is not None:
            return {'content': [{'type': 'text', 'text': 'another tool is running; retry after it finishes'}],
                    'isError': True}
        if self.pool is None:
            self.pool = self.stack.enter_context(ThreadPoolExecutor(max_workers=1))
        request = {'id': self.request_id, 'cancelled': False}
        self.active = request
        future = self.pool.submit(self._execute, request, tool, arguments, repo)
        request['future'] = future
        future.add_done_callback(lambda done: self.events.put(('done', request)))
        return request

    def _execute(self, request, tool, arguments, repo):
        with self.lock:
            if request['cancelled']:
                raise CommandCancelled('request was cancelled before it started')
            if self.owner is None:
                context = own_processes(())
                self.owner = context.__enter__()
                self.owner_context = context
            if request['cancelled']:
                raise CommandCancelled('request was cancelled during owner startup')
            owner = self.owner
        return self.run_cli(tool, arguments, repo, owner=owner)

    def _cancel(self):
        request = self.active
        if request is None:
            return
        request['cancelled'] = True
        with self.lock:
            if self.owner is not None:
                self.owner.cancel()

    def _done(self, request):
        self.active = None
        if request['cancelled']:
            self._retire_owner()
            return
        self._write(self._response(request))

    def _retire_owner(self):
        if self.owner_context is not None:
            self.owner_context.__exit__(None, None, None)
        self.owner_context = None
        self.owner = None

    def _response(self, request):
        response = {'jsonrpc': '2.0', 'id': request['id']}
        try:
            response['result'] = request['future'].result()
        except Exception as error:
            response['error'] = {'code': -32603, 'message': f'{type(error).__name__}: {error}'}
        return response

    def _write(self, response):
        try:
            self.target.write(json.dumps(response) + '\n')
            self.target.flush()
        except OSError as error:
            _silence_output(self.target)
            raise _OutputClosed from error


def _silence_output(target):
    # Prevent the interpreter's final stdout flush from repeating a broken pipe.
    try:
        with open(os.devnull, 'w') as sink:
            os.dup2(sink.fileno(), target.fileno())
    except (AttributeError, OSError):
        pass


def serve(source, target, reply, run_cli):
    try:
        return Session(source, target, reply, run_cli).run()
    except _OutputClosed:
        # A client can close its read pipe independently of stdin.
        return 0

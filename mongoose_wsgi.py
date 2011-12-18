import BaseHTTPServer as base_http_server
import logging
import socket
import sys
import time
import urllib

import mongoose


DEFAULT_ERROR_MESSAGE = """\
<html>
<head>
<title>Error response</title>
</head>
<body>
<h1>Error response</h1>
<p>Error code %(code)d.
<p>Message: %(message)s.
<p>Error code explanation: %(code)s = %(explain)s.
<pre>%(content)s</pre>
</body>
</html>
"""


def _quote_html(html):
    return html.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class WSGIEventHandler(object):
    """A request handler that implements WSGI dispatching."""

    def __init__(self, hostname, port, application):
        self.server_hostname = hostname
        self.server_port = port
        self.application = application
        self._logger = logging.getLogger('mongoose_wsgi')

    def __call__(self, event, conn, info):
        if event == mongoose.NEW_REQUEST:
            self.run_wsgi(conn, info)
        elif event == mongoose.HTTP_ERROR:
            print 'got error'
        elif event == mongoose.EVENT_LOG:
            print 'got event log'
        elif event == mongoose.INIT_SSL:
            pass
        return True

    def make_environ(self, conn, info):

        content_type = None
        try:
            content_type = conn.get_header('Content-Type')
        except:
            pass
        content_length = None
        try:
            content_length = conn.get_header('Content-Length')
        except:
            pass

        url_scheme = 'https' if info.is_ssl else 'http'
        environ = {
            'wsgi.version':         (1, 0),
            'wsgi.url_scheme':      url_scheme,
            'wsgi.input':           conn,
            'wsgi.errors':          sys.stderr,
            'wsgi.run_once':        False,
            'wsgi.multi_thread':    True,
            'wsgi.multi_process':   False,
            'SERVER_SOFTWARE':      'PyMongoose',
            'REQUEST_METHOD':       info.request_method,
            'SCRIPT_NAME':          '',
            'PATH_INFO':            urllib.unquote(info.uri),
            'QUERY_STRING':         info.query_string,
            'CONTENT_TYPE':         content_type,
            'CONTENT_LENGTH':       content_length,
            'REMOTE_ADDR':          info.remote_ip,
            'REMOTE_PORT':          info.remote_port,
            'SERVER_NAME':          self.server_hostname,
            'SERVER_PORT':          str(self.server_port),
            'SERVER_PROTOCOL':      'HTTP/%s' % (info.http_version,)
        }

        for header in info.http_headers[:info.num_headers]:
            key = 'HTTP_' + header.name.upper().replace('-', '_')
            if key not in ('HTTP_CONTENT_TYPE', 'HTTP_CONTENT_LENGTH'):
                environ[key] = header.value

        return environ

    def run_wsgi(self, conn, info):
        environ = self.make_environ(conn, info)
        headers_set = []
        headers_sent = []

        def write(data):

            assert headers_set, 'write() before start_response'
            if not headers_sent:

                status, response_headers = headers_sent[:] = headers_set
                code, msg = status.split(None, 1)
                self.send_response(conn, info, int(code), msg)
                header_keys = set()
                for key, value in response_headers:
                    self.send_header(conn, info, key, value)
                    key = key.lower()
                    header_keys.add(key)
                if 'content-length' not in header_keys:
                    # I bet this is unnecessary, as the web server should take care of it.
                    # Also, this clearly negates chunked encoding.
                    self.send_header(conn, info, 'Connection', 'close')
                if 'server' not in header_keys:
                    self.send_header(conn, info, 'Server', environ['SERVER_SOFTWARE'])
                if 'date' not in header_keys:
                    self.send_header(conn, info, 'Date', self.date_time_string())
                self.end_headers(conn, info)

            assert type(data) is str, 'applications must write bytes'
            conn.write(data)

        def start_response(status, response_headers, exc_info=None):
            if exc_info:
                try:
                    if headers_sent:
                        raise exc_info[0], exc_info[1], exc_info[2]
                finally:
                    exc_info = None
            elif headers_set:
                raise AssertionError('Headers already set')
            headers_set[:] = [status, response_headers]
            return write

        def execute(app):
            application_iter = app(environ, start_response)
            try:
                for data in application_iter:
                    write(data)
                # make sure the headers are sent
                if not headers_sent:
                    write('')
            finally:
                if hasattr(application_iter, 'close'):
                    application_iter.close()
                application_iter = None

        try:
            execute(self.application)
        except (socket.error, socket.timeout), e:
            self.connection_dropped(e, environ)
        except Exception as e:
            from werkzeug.debug.tbtools import get_current_traceback
            traceback = get_current_traceback(ignore_system_exceptions=True)
            self.send_error(conn, info, 500, content=str(traceback.plaintext))

            # from werkzeug.debug.tbtools import get_current_traceback
            # traceback = get_current_traceback(ignore_system_exceptions=True)
            # try:
            #     # if we haven't yet sent the headers but they are set
            #     # we roll back to be able to set them again.
            #     if not headers_sent:
            #         del headers_set[:]
            #     execute(InternalServerError())
            # except Exception:
            #     pass
            # self.log_error('Error on request:\n%s',
            #                 traceback.plaintext)

    def send_header(self, conn, info, keyword, value):
        """Send a MIME header."""
        if info.http_version != 'HTTP/0.9':
            conn.printf('%s', "%s: %s\r\n" % (keyword, value))

    def end_headers(self, conn, info):
        """Send the blank line ending the MIME headers."""
        if info.http_version != 'HTTP/0.9':
            conn.printf("\r\n")

    def send_response(self, conn, info, code, message=None):
        """Send the response header and log the response code."""
        if message is None:
            message = ''
            if code in base_http_server.BaseHTTPRequestHandler.responses:
                message = base_http_server.BaseHTTPRequestHandler.responses[code]
        if info.http_version != 'HTTP/0.9':
            conn.printf('%s', "HTTP/%s %d %s\r\n" % (info.http_version, code, message))

    def send_error(self, conn, info, code, message='', content=''):
        """Send and log an error reply.

        Arguments are the error code, and a detailed message.
        The detailed message defaults to the short entry matching the
        response code.

        This sends an error response (so it must be called before any
        output has been generated), logs the error, and finally sends
        a piece of HTML explaining the error to the user.

        """

        try:
            short, long = base_http_server.BaseHTTPRequestHandler.responses[code]
        except KeyError:
            short, long = '???', '???'
        if not message:
            message = short
        explain = long
        self.log_error("code %d, message %s", code, message)

        response = DEFAULT_ERROR_MESSAGE % {'code': code,
                                           'message': _quote_html(message),
                                           'explain': explain,
                                           'content': content}
        self.send_response(conn, info, code, message)
        self.send_header(conn, info, "Content-Type", 'text/html')

        self.send_header(conn, info, 'Content-Length', str(len(response)))
        self.send_header(conn, info, 'Connection', 'close')
        self.end_headers(conn, info)

        if info.request_method != 'HEAD' and code >= 200 and code not in (204, 304):
            conn.write(response)

    def log_date_time_string(self):
        """Return the current time formatted for logging."""
        return time.strftime('%d/%b/%Y %H:%M:%S')

    def date_time_string(self, timestamp=None):
        """Return the current date and time formatted for a message header."""
        if timestamp is None:
            timestamp = time.localtime()
        return time.strftime('%a, %d %b %Y %H:%M:%S', timestamp)

    def log_request(self, uri, code='-', size='-'):
        self.log('info', '"%s" %s %s', uri, code, size)

    def log_error(self, *args):
        print args
        self.log('error', *args)

    def log_message(self, format, *args):
        self.log('info', format, *args)

    def log(self, type, message, *args):
        print message % args
        fmessage = '%s - - [%s] %s\n' % (self.server_hostname,
                                         self.log_date_time_string(),
                                         message % args)
        getattr(self._logger, type)(fmessage.rstrip())

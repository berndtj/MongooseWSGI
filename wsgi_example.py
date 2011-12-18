import logging
import sys

from wsgiref.util import setup_testing_defaults

import mongoose
import mongoose_wsgi

logging.basicConfig(level=logging.DEBUG)


def simple_app(environ, start_response):
    setup_testing_defaults(environ)
    raise Exception('Holla!')

    status = '200 OK'
    headers = [('Content-type', 'text/plain')]

    start_response(status, headers)

    ret = ["%s: %s\n" % (key, value)
           for key, value in environ.iteritems()]
    return ret


def main():
    # Create mongoose object, and register '/foo' URI handler
    # List of options may be specified in the contructor
    handler = mongoose_wsgi.WSGIEventHandler('localhost', 8080, simple_app)

    server = mongoose.Mongoose(handler,
                               document_root='/tmp',
                               listening_ports='8080',
                               num_threads='2',)

    print ('Mongoose started on port %s, press enter to quit'
           % server.get_option('listening_ports'))

    sys.stdin.read(1)

    # Deleting server object stops all serving threads
    print 'Stopping server.'
    del server


if __name__ == '__main__':
    main()

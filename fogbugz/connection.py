#   Copyright (C) 2010 Henry Ludemann <misc@hl.id.au>
#
#   This file is part of the fogbugz import/export library.
#
#   The fogbugz import/export library is free software; you can redistribute it
#   and/or modify it under the terms of the GNU Lesser General Public
#   License as published by the Free Software Foundation; either
#   version 2.1 of the License, or (at your option) any later version.
#
#   The fogbugz import/export library is distributed in the hope that it will be
#   useful, but WITHOUT ANY WARRANTY; without even the implied warranty
#   of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
#   Lesser General Public License for more details.
#
#   You should have received a copy of the GNU Lesser General Public
#   License along with this library; if not, see
#   <http://www.gnu.org/licenses/>.

import httplib
import logging
import random
import socket
import string
from StringIO import StringIO
import sys
import urllib
import urlparse
from xml.etree import ElementTree
from xml.parsers.expat import ExpatError

class BaseConnection:
    def __init__(self, server, name=None):
        self._server = server
        self._name = name or 'fogbugz'

    def logon(self):
        self._token = None
        self._token = self.post('logon', {'email':self._server.username, 'password':self._server.password}, element='token').text

    def _post(self, args, files):
        """Post a request to the fogbugz server."""
        raise NotImplementedError()

    def post(self, cmd, args, files=[], element=None):
        """Post a single change to fogbugz.

        cmd -- The command to run (eg: edit, close, reopen, ...).
        files -- A list of (filename, contents) tuples.
        element -- An xml selector to return.
        return -- An ElementTree instance for the FogBugz result. 
        """
        if self._token is not None:
            args['token'] = self._token
        if files:
            args['nFileCount'] = len(files)
        args['cmd'] = cmd

        # Log a debug message to show what is going on...
        temp = list(args.items())
        temp.sort()
        logging.debug('%s - %s %s', self._name, temp, [filename for filename, contents in files])

        xml = self._post(args, files)
        return self._get_element(xml, element)

    def _get_attachment(self, url):
        raise NotImplementedError()

    def get_attachment(self, path):
        url = self._server.path + path + '&token=' + self._token
        logging.info('Asking for attachment at %s', url)
        return self._get_attachment(url)

    def _get_element(self, xml, element):
        try:
            tree = ElementTree.parse(StringIO(xml)).getroot()
        except ExpatError, ex:
            logging.debug('%s', xml)
            sys.exit(str(ex))
        if tree.find('error') is not None:
            sys.exit(xml)
        if element is not None:
            tree = tree.find(element)
            if tree is None:
                sys.exit("Failed to find element '%s' in:\n%s" % (element, xml))
        return tree;


class Connection(BaseConnection):
    def __init__(self, hostaddress, name=None):
        self._connection = None
        self._username = None
        self._password = None

        server = urlparse.urlparse(hostaddress)
        while not server.username:
            server.username = raw_input('Fogbugz username: ')
        while not server.password:
            server.password = getpass.getpass('Enter Fogbugz admin password:')

        BaseConnection.__init__(self, server, name)
        self._reconnect()

    def _reconnect(self):
        if self._server is not None:
            if not self._server.scheme or self._server.scheme == 'http':
                self.connection = httplib.HTTPConnection(self._server.hostname, self._server.port)
            elif self._server.scheme == 'https':
                self.connection = httplib.HTTPSConnection(self._server.hostname, self._server.port)
            else:
                sys.exit("Unknown server scheme '%s'!" % self._server.scheme)

            # Request the 'live' url
            self.connection.request('GET', self._server.path + '/api.xml')
            self._http_path = '/%s' % self._get_element(self._get_response(), 'url').text
        self.logon()

    def _post(self, args, files):
        return self._post_multipart("POST", self._http_path, args.items(),
                [('File%i' % (i+1), name, contents) for i, (name, contents) in enumerate(files)])
               
    def _get_attachment(self, url):
        self.connection.request('GET', url)
        return self._get_response()

    def _post_multipart(self, host, selector, fields, files):
        content_type, body = self._encode_multipart_formdata(fields, files)
        while 1:
            try:
                self.connection.putrequest('POST', selector)
                self.connection.putheader('content-type', content_type)
                self.connection.putheader('content-length', len(body))
                self.connection.endheaders()
                self.connection.send(body)
                return self._get_response()
            except socket.error, ex:
                logging.error('Socket error (%s); logging in again...', ex)
                self._reconnect()
            except httplib.HTTPException, ex:
                logging.error('Http error (%s); logging in again...', ex)
                self._reconnect()

    def _encode_multipart_formdata(self, fields, files):
        boundary = '----------' + ''.join(
                string.ascii_letters[random.randint(0, len(string.ascii_letters)-1)]
                for i in range(16))
        CRLF = '\r\n'
        L = []
        for (key, value) in fields:
            L.append('--' + boundary)
            L.append('Content-Disposition: form-data; name="%s"' % key)
            L.append('')
            L.append(value)
        for (key, filename, value) in files:
            L.append('--' + boundary)
            L.append('Content-Disposition: form-data; name="%s"; filename="%s"' % (key, filename))
            L.append('Content-Type: application/octet-stream')
            L.append('')
            L.append(value)
        L.append('--' + boundary + '--')
        L.append('')
        body = CRLF.join(str(l) if not isinstance(l, unicode) else l.encode('utf8') for l in L)
        content_type = 'multipart/form-data; boundary=%s' % boundary
        return content_type, body

    def _get_response(self):
        response = self.connection.getresponse()
        if response.status != 200:
            sys.exit('Fogbugz server failure %i: %s' % (response.status, response.reason))
        return response.read()


class MockConnection(BaseConnection):
    def __init__(self, name=None, search=None):
        logging.info('IMPORTANT: Fogbugz server not specifed! Printing issues to stdout.')
        self._search = search
        BaseConnection.__init__(self, urlparse.urlparse('test://username:password@server/'), name)
        self.logon()

    def _search(self):
        raise NotImplementedError()

    def _post(self, args, files=[]):
        cmd = args['cmd']
        if cmd == 'logon':
            return '<response><token>%i</token></response>' % random.randint(0, 1000)
        elif cmd == 'newProject':
            return '<response><project><ixProject>%i</ixProject></project></response>' % random.randint(0, 1000)
        elif cmd == 'newPerson':
            return '<response><person><ixPerson>%i</ixPerson></person></response>' % random.randint(0, 1000)
        elif cmd == 'new':
            return '<response><case ixBug="%i" /></response>' % random.randint(0, 1000)
        elif cmd in ['edit', 'close', 'resolve', 'reactivate']:
            return '<response><case ixBug="1234" /></response>'
        elif cmd == 'listPeople':
            return '<response />'
        elif cmd == 'listProjects':
            return '<response />'
        elif cmd == 'search':
            return self._search or '<response/>'
        else:
            raise Exception('%s not handled in test...' % cmd)

